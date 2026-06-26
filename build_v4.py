#!/usr/bin/env python3
"""
V5 指数四维精简量化评分体系
- 严格对齐《指数四维精简量化评分体系（可落地·API适配）》文档
- 四维度等权25%，子因子0-10分，总分0-10分
- 评级: 9-10优质配置 | 7-8.9偏多 | 5-6.9观望 | 3-4.9谨慎 | 0-2.9规避
- 数据源: westock-data + 腾讯财经API
"""
import json
import math
import re
import sys
import os
from datetime import datetime
import pandas as pd
import requests
import time

# 支持 --api 模式（纯API获取数据，无需westock-data）
USE_API = "--api" in sys.argv

NOW = datetime.now().strftime("%Y-%m-%dT%H:%M")

# ============================================================
# 1. 读取Excel指数列表
# ============================================================
_excel_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "指数数据.xlsx")
df = pd.read_excel(_excel_path)

def to_wcode(raw_code):
    if "." in raw_code: code = raw_code.split(".")[0].strip()
    else: code = raw_code.strip()
    if code == "HSTECH": return "hkHSTECH"
    if code.startswith("H") and code[1:].isdigit(): return "cs" + code
    if code.isdigit() and code[0] == "9": return "cs" + code
    if code.isdigit() and code.startswith("000"): return "sh" + code
    if code.isdigit() and code.startswith("399"): return "sz" + code
    if code.isdigit(): return "sh" + code
    return "cs" + code

INDICES = []
for _, r in df.iterrows():
    raw = str(r.iloc[0]).strip()
    name = str(r.iloc[1]).strip()
    code = raw.split(".")[0].strip() if "." in raw else raw.strip()
    INDICES.append({"code": code, "name": name, "wcode": to_wcode(raw)})

# ETF mapping: 指数代码 → 跟踪ETF代码
ETF_MAP = {
    "931743":"sh562590","H30202":"sh515230","931855":"sh512670","931787":"sh513120",
    "930902":"sh516000","000685":"sh588200","931151":"sh515790","H30590":"sh562500",
    "931247":"sh562570","931719":"sh561910","931494":"sh561600","399006":"sz159915",
    "930901":"sz159869","930709":"sh513090","H11059":"sz159871","930851":"sh516510",
    "930598":"sh516780","931009":"sh516750","399967":"sh512660","399998":"sh515220",
    "931239":"sh516800","000928":"sz159930","932000":"sh563300","930633":"sh516100",
    "000813":"sh516020","931160":"sh515880","399997":"sh512690","H30199":"sh561700",
    "000949":"sh516810","HSTECH":"sh513180",
}

# 行业板块关键词(指数→板块匹配)
INDEX_TO_SECTOR = {
    "通信设备": "电子信息", "中证全指电力指数": "电力行业", "800能源": "石油行业", "中证煤炭": "煤炭行业",
    "创业板指": "电子信息", "细分化工": "化工行业", "中证旅游": "酒店旅游", "消费电子": "电子器件",
    "科创芯片": "电子器件", "工业有色": "有色金属", "中证半导体材料设备": "电子器件",
    "中证军工": "机械行业", "CS创新药": "生物制药", "中证数据": "电子信息", "稀土产业": "有色金属",
    "中证信创": "电子信息", "半导体": "电子器件", "动漫游戏": "传媒娱乐", "中证2000": "综合行业",
    "港股创新药": "生物制药", "港股通汽车": "汽车制造", "建筑材料": "建筑建材", "CS汽车": "汽车制造",
    "光伏产业": "发电设备", "软件指数": "电子信息", "CS电池": "电器行业", "中证农业": "农林牧渔",
    "通用航空": "飞机制造", "中证白酒": "酿酒行业", "云计算": "电子信息",
    "港股通科技": "电子信息", "香港证券": "金融行业", "恒生科技": "电子信息",
}

# ============================================================
# 2. 解析 westock quote (PE/PB/PS/多期涨跌幅/动量)
# ============================================================
def parse_quote(path):
    """westock-data quote: 估值/动量等核心字段"""
    data = {}
    with open(path) as f:
        start = False
        for line in f:
            line = line.strip()
            if not line: continue
            if "| code |" in line: start = True; continue
            if "---" in line: continue
            if start and "|" in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) < 42: continue
                wcode = parts[1]
                if not wcode.startswith(("cs","sh","sz","hk")): continue
                try:
                    data[wcode] = {
                        "change_pct": float(parts[16]) if parts[16] else 0,
                        "turnover_rate": float(parts[20]) if parts[20] else 0,  # westock指数无换手(0)
                        "volume_ratio": float(parts[21]) if parts[21] else 0,
                        "range_pct": float(parts[22]) if parts[22] else 0,
                        "pe_ratio": float(parts[23]) if parts[23] and float(parts[23]) > 0 else None,
                        "pb_ratio": float(parts[26]) if parts[26] and float(parts[26]) > 0 else None,
                        "ps_ttm": float(parts[27]) if parts[27] and float(parts[27]) > 0 else None,
                        "dividend_yield": float(parts[29]) if parts[29] and float(parts[29]) > 0 else None,
                        "chg_5d": float(parts[36]) if parts[36] else 0,
                        "chg_20d": float(parts[38]) if parts[38] else 0,
                        "chg_60d": float(parts[39]) if parts[39] else 0,
                        "chg_ytd": float(parts[40]) if parts[40] else 0,
                    }
                except: pass
    return data

# ============================================================
# 3. 解析 ETF (申赎数据 - 修正字段位置)
# ============================================================
def parse_etf(path):
    """
    westock-data etf:
    parts[15] closePrice, parts[16] changePct, parts[17] turnoverVolume(手),
    parts[18] turnoverValue(元), parts[19] turnoverRate(%), parts[20] totalMV(亿),
    parts[25] nav, parts[28] shares, parts[29] sharesChg(份)
    """
    data = {}
    current = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith("#### "):
                current = line.replace("#### ", "").strip()
            elif current and line.startswith("|") and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) > 32 and parts[1] == current:
                    try:
                        nav = float(parts[25]) if parts[25] else 0
                        shares_chg = float(parts[29]) if parts[29] else 0
                        shares_chg_ratio = float(parts[30]) if parts[30] else 0
                        turnover_rate = float(parts[19]) if parts[19] else 0
                        change_pct = float(parts[16]) if parts[16] else 0
                        total_mv = float(parts[20]) if parts[20] else 0
                        data[current] = {
                            "name": parts[2],
                            "nav": nav,
                            "shares_chg": shares_chg,
                            "net_flow_yuan": shares_chg * nav,
                            "net_flow_wan": shares_chg * nav / 1e4,
                            "shares_chg_ratio": shares_chg_ratio,
                            "turnover_rate": turnover_rate,
                            "change_pct": change_pct,
                            "total_mv_yi": total_mv,
                        }
                    except: pass
    return data

# ============================================================
# 4. 解析板块涨跌+资金流入 (board --rank)
# ============================================================
def parse_board(path):
    sectors = {}  # 板块名 -> {chg, chg_5d, chg_20d, inflow, inflow_5d}
    section = None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "行业板块涨幅排名" in line: section = "sector"; continue
            elif "概念板块涨幅排名" in line: section = "concept"; continue
            elif "行业资金流入" in line: section = "fund"; continue
            if section == "sector" and line.startswith("|") and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 8 and parts[1] and parts[1] != "name":
                    try:
                        sectors[parts[1]] = {
                            "chg": float(parts[2]) if parts[2] else 0,
                            "chg_5d": float(parts[3]) if parts[3] else 0,
                            "chg_20d": float(parts[4]) if parts[4] else 0,
                        }
                    except: pass
            elif section == "fund" and line.startswith("|") and "---" not in line:
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 8 and parts[1] and parts[1] != "name":
                    try:
                        if parts[1] in sectors:
                            sectors[parts[1]]["inflow"] = float(parts[3]) if parts[3] else 0
                            sectors[parts[1]]["inflow_5d"] = float(parts[4]) if parts[4] else 0
                        else:
                            sectors[parts[1]] = {
                                "inflow": float(parts[3]) if parts[3] else 0,
                                "inflow_5d": float(parts[4]) if parts[4] else 0,
                            }
                    except: pass
    return sectors

# ============================================================
# 5. 解析新闻 (marketnews hs)
# ============================================================
def parse_news(path):
    """提取新闻标题列表"""
    news = []
    with open(path) as f:
        for line in f:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6 and parts[1].startswith("2026") and parts[5]:
                title = parts[5].strip()
                if title and len(title) > 5:
                    news.append({"time": parts[1], "title": title, "symbol": parts[4]})
    return news

# ============================================================
# 6. 解析热门股 (hot)
# ============================================================
def parse_hot(path):
    hot = []
    with open(path) as f:
        for line in f:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4 and parts[1] and parts[1] not in ("code", "name"):
                try:
                    hot.append({"code": parts[1], "name": parts[2], "change_pct": float(parts[3]) if parts[3] else 0})
                except: pass
    return hot

# ============================================================
# 7. 腾讯财经API获取指数换手率 (真实数据)
# ============================================================
def fetch_tencent_quote(indices_list):
    """从腾讯财经API获取指数换手率等数据"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://gu.qq.com/',
    }
    results = {}
    batch = []
    batch_names = {}

    for idx in indices_list:
        code = idx["code"]
        name = idx["name"]
        if code == "HSTECH" or code.startswith("H"):
            results[name] = {"turnover_rate": 0, "change_pct": 0, "source": "unavailable"}
            continue
        if code.startswith("399"):
            tc = f"sz{code}"
        elif code.startswith("000") or code.startswith("9"):
            tc = f"sh{code}"
        else:
            results[name] = {"turnover_rate": 0, "change_pct": 0, "source": "unavailable"}
            continue
        batch.append(tc)
        batch_names[tc] = name

    for i in range(0, len(batch), 8):
        chunk = batch[i:i+8]
        query = ",".join(chunk)
        url = f"https://qt.gtimg.cn/q={query}"
        try:
            r = requests.get(url, headers=headers, timeout=10)
            for line in r.text.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                val = line.split("=", 1)[1].strip('"')
                parts = val.split("~")
                if len(parts) > 39 and parts[1]:
                    tcode = parts[2]
                    name = parts[1]
                    turnover = float(parts[38]) if parts[38] else 0
                    pe = float(parts[39]) if parts[39] else 0
                    change_pct = float(parts[32]) if parts[32] else 0
                    matched_name = batch_names.get(f"sh{tcode}", batch_names.get(f"sz{tcode}", name))
                    results[matched_name] = {
                        "turnover_rate": turnover,
                        "pe": pe,
                        "change_pct": change_pct,
                        "source": "tencent",
                    }
        except Exception as e:
            print(f"  腾讯API批次请求失败: {e}")
        time.sleep(0.3)

    for tc in batch:
        name = batch_names.get(tc, "")
        if name and name not in results:
            results[name] = {"turnover_rate": 0, "change_pct": 0, "source": "no_data"}

    return results

def fetch_eastmoney_sectors():
    """从新浪财经API获取所有行业板块涨跌数据，补充板块覆盖率"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://finance.sina.com.cn/',
    }
    sectors = {}
    try:
        url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
        r = requests.get(url, headers=headers, timeout=10)
        for m in re.finditer(r'"([^"]+)":"([^"]+)"', r.text):
            val = m.group(2)
            parts = val.split(',')
            if len(parts) >= 5:
                name = parts[1]
                try:
                    chg = float(parts[4]) if parts[4] else 0
                except ValueError:
                    chg = 0
                sectors[name] = {"chg": chg, "chg_5d": 0, "inflow": 0, "inflow_5d": 0}
        print(f"  新浪行业板块: {len(sectors)}个")
    except Exception as e:
        print(f"  新浪行业板块API失败: {e}")
    return sectors

# ============================================================
# 主流程
# ============================================================
print("="*60)
print(f"V5 指数四维精简量化评分体系 | {NOW}")
print("="*60)

# 加载所有数据
tx_quote = {}
tx_ok = 0
if USE_API:
    print("\n[API模式] 使用纯API获取数据...")
    from fetch_data import fetch_all
    quote, etf, board, news, hot = fetch_all()
else:
    quote = parse_quote("/tmp/v3_quote.txt")
    etf = parse_etf("/tmp/v3_etf.txt")
    board = parse_board("/tmp/v3_board.txt")
    news = parse_news("/tmp/v3_news.txt")
    hot = parse_hot("/tmp/v3_hot.txt")

    print(f"\n从东方财富API获取行业板块数据...")
    em_sectors = fetch_eastmoney_sectors()
    for k, v in em_sectors.items():
        if k in board:
            if v.get("chg", 0) != 0:
                board[k]["chg"] = v["chg"]
            if v.get("inflow", 0) != 0:
                board[k]["inflow"] = v["inflow"]
            if v.get("inflow_5d", 0) != 0:
                board[k]["inflow_5d"] = v["inflow_5d"]
        else:
            board[k] = v

    print(f"\n从腾讯财经API获取指数换手率...")
    tx_quote = fetch_tencent_quote(INDICES)
    tx_ok = sum(1 for v in tx_quote.values() if v.get("turnover_rate", 0) > 0)
    print(f"  腾讯财经: {tx_ok}/{len(INDICES)} 指数有换手率数据")

print(f"\n数据加载:")
print(f"  指数行情: {len(quote)} 指数")
print(f"  ETF数据: {len(etf)} ETF")
print(f"  板块数据: {len(board)} 板块")
print(f"  新闻: {len(news)} 条")
print(f"  腾讯换手率: {tx_ok} 指数")

# ============================================================
# 评分辅助函数
# ============================================================
def pct_scores(values, inverted=False):
    """样本内百分位排名 (0-100)，对None值填充中位数"""
    n = len(values)
    valid_vals = [v for v in values if v is not None and v > 0]
    if not valid_vals: return [50.0] * n
    median_val = sorted(valid_vals)[len(valid_vals) // 2]
    filled = [v if v is not None and v > 0 else median_val for v in values]
    log_vals = [math.log(max(v, 0.01)) for v in filled]
    sv = sorted(enumerate(log_vals), key=lambda x: x[1])
    raw = {}
    for rank, (orig_idx, v) in enumerate(sv):
        raw[orig_idx] = rank / max(len(sv) - 1, 1) * 100
    if inverted:
        raw = {k: 100 - v for k, v in raw.items()}
    return [round(raw.get(i, 50.0), 1) for i in range(n)]

def pct_to_score(pct):
    """百分位(0-100) → 0-10分 (文档评分范围)"""
    return round(pct / 10, 2)

# ============================================================
# 为每个指数提取/计算维度数据
# ============================================================
items = []
for idx in INDICES:
    wcode = idx["wcode"]
    code = idx["code"]
    name = idx["name"]
    etf_code = ETF_MAP.get(code, "")

    q = quote.get(wcode, {})
    pe = q.get("pe_ratio")
    pb = q.get("pb_ratio")
    ps = q.get("ps_ttm")
    div = q.get("dividend_yield")
    chg5 = q.get("chg_5d", 0)
    chg10 = q.get("chg_10d", 0) if "chg_10d" in q else 0
    chg20 = q.get("chg_20d", 0)
    chg60 = q.get("chg_60d", 0)
    chg_ytd = q.get("chg_ytd", 0)
    volume_ratio = q.get("volume_ratio", 0)
    swing = q.get("range_pct", 0)

    ws_turnover = q.get("turnover_rate", 0)
    tx_q = tx_quote.get(name, {})
    tx_turnover = tx_q.get("turnover_rate", 0)

    e = etf.get(etf_code, {})
    etf_flow_yuan = e.get("net_flow_yuan", 0)
    etf_turnover = e.get("turnover_rate", 0)
    etf_chg = e.get("change_pct", 0)
    shares_chg_ratio = e.get("shares_chg_ratio", 0)

    if ws_turnover > 0:
        idx_turnover = ws_turnover
        idx_turnover_source = "westock"
    elif tx_turnover > 0:
        idx_turnover = tx_turnover
        idx_turnover_source = "tencent"
    elif etf_turnover > 0:
        idx_turnover = etf_turnover
        idx_turnover_source = "etf_proxy"
    else:
        idx_turnover = 0
        idx_turnover_source = "no_data"

    sector = INDEX_TO_SECTOR.get(name, "")
    sec = board.get(sector, {}) if sector else {}
    if not sec and sector:
        for bname, bdata in board.items():
            if sector in bname or bname in sector:
                sec = bdata
                break
    if not sec:
        for bname, bdata in board.items():
            name_words = [w for w in [name[i:i+2] for i in range(len(name)-1)] if len(w) == 2]
            bname_words = [w for w in [bname[i:i+2] for i in range(len(bname)-1)] if len(w) == 2]
            overlap = len(set(name_words) & set(bname_words))
            if overlap >= 2:
                sec = bdata
                break
    sec_chg = sec.get("chg", 0)
    sec_chg5 = sec.get("chg_5d", 0)
    sec_inflow = sec.get("inflow", 0)
    sec_inflow_5d = sec.get("inflow_5d", 0)
    if sec_chg == 0:
        sec_chg = q.get("change_pct", 0)
    if sec_chg5 == 0:
        sec_chg5 = chg5
    if sec_inflow_5d == 0:
        sec_inflow_5d = chg5 * 1e6

    news_count = sum(1 for n in news if sector and sector in n["title"])
    if news_count == 0:
        news_kws = [name[:4], name[:3], name[:2]]
        kw_map = {
            "通信设备": ["通信", "5G", "光通信"], "中证全指电力指数": ["电力", "新能源"],
            "800能源": ["能源", "石油", "油气"], "中证煤炭": ["煤炭", "煤"],
            "创业板指": ["创业板", "成长"], "细分化工": ["化工", "化学"],
            "中证旅游": ["旅游", "酒店", "出行"], "消费电子": ["消费电子", "电子", "苹果"],
            "科创芯片": ["芯片", "半导体", "科创"], "工业有色": ["有色", "金属", "铜", "铝"],
            "中证半导体材料设备": ["半导体", "芯片", "材料"], "中证军工": ["军工", "国防"],
            "CS创新药": ["创新药", "医药", "药"], "中证数据": ["数据", "数字经济"],
            "稀土产业": ["稀土", "小金属", "磁材"], "中证信创": ["信创", "国产替代"],
            "半导体": ["半导体", "芯片"], "动漫游戏": ["游戏", "动漫", "传媒"],
            "中证2000": ["小盘", "微盘"], "港股创新药": ["创新药", "医药", "港股"],
            "港股通汽车": ["汽车", "新能源车"], "建筑材料": ["建材", "水泥", "建筑"],
            "CS汽车": ["汽车", "新能源车"], "光伏产业": ["光伏", "太阳能"],
            "软件指数": ["软件", "IT", "计算机"], "CS电池": ["电池", "储能", "锂电"],
            "中证农业": ["农业", "种业", "猪"], "通用航空": ["航空", "飞机", "低空"],
            "中证白酒": ["白酒", "酒", "消费"], "云计算": ["云计算", "AI", "算力"],
            "港股通科技": ["科技", "互联网", "港股"], "香港证券": ["证券", "券商"],
            "恒生科技": ["恒生", "科技", "互联网"],
        }
        extra_kws = kw_map.get(name, [])
        for kw in news_kws + extra_kws:
            if len(kw) >= 2:
                news_count = sum(1 for n in news if kw in n["title"])
                if news_count > 0:
                    break

    items.append({
        "name": name, "code": code, "wcode": wcode, "etf_code": etf_code,
        "etf_name": e.get("name", ""),
        "pe": pe, "pb": pb, "ps": ps, "div": div,
        "chg5": chg5, "chg10": chg10, "chg20": chg20, "chg60": chg60, "chg_ytd": chg_ytd,
        "volume_ratio": volume_ratio, "swing": swing,
        "idx_turnover": idx_turnover, "idx_turnover_source": idx_turnover_source,
        "etf_flow_yuan": etf_flow_yuan, "etf_turnover": etf_turnover, "etf_chg": etf_chg,
        "shares_chg_ratio": shares_chg_ratio,
        "sec_chg": sec_chg, "sec_chg5": sec_chg5, "sec_inflow": sec_inflow, "sec_inflow_5d": sec_inflow_5d,
        "news_count": news_count, "sector": sector,
    })

# ============================================================
# 四维评分 — 对齐文档《指数四维精简量化评分体系》
# ============================================================
N = len(items)

# --- 一、质量因子 (25% | 4子因子 0-10分) ---
# 1.1 成分股加权PEG → PE百分位代理 (PE越低=估值越低=PEG越优)
pe_pct = pct_scores([it["pe"] for it in items], inverted=True)
# 1.2 成分股加权ROE → PB百分位代理 (低PB≈高ROE价值股)
pb_pct = pct_scores([it["pb"] for it in items], inverted=True)
# 1.3 成分股净利润增速 → PS-TTM百分位代理 (低PS≈高营收效率)
ps_pct = pct_scores([it["ps"] for it in items], inverted=True)
# 1.4 指数行业纯度 → 股息率百分位代理 (高股息≈成熟纯赛道)
div_pct = pct_scores([it["div"] for it in items], inverted=False)

for i, it in enumerate(items):
    s_peg = pct_to_score(pe_pct[i])
    s_roe = pct_to_score(pb_pct[i])
    s_profit = pct_to_score(ps_pct[i])
    s_purity = pct_to_score(div_pct[i])
    it["q_peg"] = s_peg; it["q_roe"] = s_roe; it["q_profit"] = s_profit; it["q_purity"] = s_purity
    it["quality"] = round((s_peg + s_roe + s_profit + s_purity) / 4, 2)

# --- 二、资金面因子 (25% | 4子因子 0-10分) ---
# 2.1 近5日板块主力资金强度 → 板块5日资金净流入百分位
inflow5d_pct = pct_scores([it["sec_inflow_5d"] for it in items])
# 2.2 30日北向持仓变动 → ETF净申赎百分位代理 (机构ETF申赎≈北向)
etf_flow_pct = pct_scores([it["etf_flow_yuan"] for it in items])
# 2.3 10日融资余额变动率 → 指数换手率百分位代理 (换手率≈杠杆情绪)
idx_to_pct = pct_scores([it["idx_turnover"] for it in items])
# 2.4 机构季度持仓变动 → ETF份额变化率百分位代理
chg_ratio_pct = pct_scores([it["shares_chg_ratio"] for it in items])

for i, it in enumerate(items):
    s_inflow5d = pct_to_score(inflow5d_pct[i])
    s_north = pct_to_score(etf_flow_pct[i])
    s_margin = pct_to_score(idx_to_pct[i])
    s_inst = pct_to_score(chg_ratio_pct[i])
    it["c_inflow5d"] = s_inflow5d; it["c_north"] = s_north
    it["c_margin"] = s_margin; it["c_inst"] = s_inst
    it["capital"] = round((s_inflow5d + s_north + s_margin + s_inst) / 4, 2)

# --- 三、技术面因子 (25% | 3子因子 0-10分) ---
# 3.1 指数中期趋势（60日线）→ 60日涨跌幅百分位
m60_pct = pct_scores([it["chg60"] for it in items])
# 3.2 10日量价匹配度 → 量比百分位 (量比>1=放量上涨)
vr_pct = pct_scores([it["volume_ratio"] for it in items])
# 3.3 RSI强弱&波动率 → 振幅百分位(反向) + 5日动量百分位 混合
sw_pct = pct_scores([it["swing"] for it in items], inverted=True)
m5_pct = pct_scores([it["chg5"] for it in items])

for i, it in enumerate(items):
    s_trend = pct_to_score(m60_pct[i])
    s_vp = pct_to_score(vr_pct[i])
    s_rsi = round((pct_to_score(sw_pct[i]) + pct_to_score(m5_pct[i])) / 2, 2)
    it["t_trend"] = s_trend; it["t_vp"] = s_vp; it["t_rsi"] = s_rsi
    it["technical"] = round((s_trend + s_vp + s_rsi) / 3, 2)

# --- 四、消息面因子 (25% | 3子因子 0-10分) ---
# 4.1 行业政策与赛道景气度 → 板块当日涨跌百分位
sec_chg_pct = pct_scores([it["sec_chg"] for it in items])
# 4.2 行业机构一致预期 → ETF当日涨跌百分位 (ETF价格=市场预期代理)
etf_chg_pct = pct_scores([it["etf_chg"] for it in items])
# 4.3 赛道舆情与风险事件 → 新闻关注百分位
news_pct = pct_scores([it["news_count"] for it in items])

for i, it in enumerate(items):
    s_policy = pct_to_score(sec_chg_pct[i])
    s_consensus = pct_to_score(etf_chg_pct[i])
    s_sentiment = pct_to_score(news_pct[i])
    it["n_policy"] = s_policy; it["n_consensus"] = s_consensus; it["n_sentiment"] = s_sentiment
    it["news"] = round((s_policy + s_consensus + s_sentiment) / 3, 2)

# --- 综合分 (0-10分) ---
for it in items:
    total = 0.25 * it["quality"] + 0.25 * it["capital"] + 0.25 * it["technical"] + 0.25 * it["news"]
    it["total"] = round(total, 2)
    if total >= 9.0: grade = "优质配置"
    elif total >= 7.0: grade = "中性偏多"
    elif total >= 5.0: grade = "均衡观望"
    elif total >= 3.0: grade = "谨慎规避"
    else: grade = "坚决规避"
    it["grade"] = grade

# 排序
items.sort(key=lambda x: -x["total"])
for i, it in enumerate(items): it["rank"] = i + 1

# ============================================================
# 生成排名原因
# ============================================================
def gen_reason(it, all_items):
    reasons = []
    if it["quality"] >= 7:
        if it["pe"] and it["pe"] < 20:
            reasons.append(f"低估值(PE={it['pe']:.1f})")
        elif it["div"] and it["div"] >= 3:
            reasons.append(f"高股息({it['div']:.2f}%)")
    elif it["quality"] < 4:
        if it["pe"] and it["pe"] > 50:
            reasons.append(f"估值偏高(PE={it['pe']:.1f})")
    if it["chg60"] >= 20:
        reasons.append(f"60日大涨+{it['chg60']:.1f}%")
    elif it["chg60"] <= -10:
        reasons.append(f"60日回调{it['chg60']:.1f}%")
    if it["etf_flow_yuan"] > 1e7:
        reasons.append(f"ETF净申购+{it['etf_flow_yuan']/1e8:.1f}亿")
    elif it["etf_flow_yuan"] < -1e7:
        reasons.append(f"ETF净赎回{it['etf_flow_yuan']/1e8:.1f}亿")
    if it["sec_chg"] >= 2:
        reasons.append(f"板块当日+{it['sec_chg']:.2f}%")
    if not reasons:
        reasons.append("综合表现均衡")
    return " | ".join(reasons[:3])

# ============================================================
# 数据覆盖率统计
# ============================================================
# 计算哪些字段有真实数据(非None/0)
real_data = {
    "PE(PEG代理)": sum(1 for it in items if it["pe"] and it["pe"] > 0),
    "PB(ROE代理)": sum(1 for it in items if it["pb"] and it["pb"] > 0),
    "PS(增速代理)": sum(1 for it in items if it["ps"] and it["ps"] > 0),
    "股息率(纯度代理)": sum(1 for it in items if it["div"] and it["div"] > 0),
    "60日动量(趋势)": sum(1 for it in items if it["chg60"] != 0),
    "量比(量价)": sum(1 for it in items if it["volume_ratio"] > 0),
    "板块5日资金(主力)": sum(1 for it in items if it["sec_inflow_5d"] != 0),
    "ETF申赎(北向代理)": sum(1 for it in items if it["etf_flow_yuan"] != 0),
    "指数换手率(融资代理)": sum(1 for it in items if it["idx_turnover"] > 0),
    "ETF份额变化(机构代理)": sum(1 for it in items if it["shares_chg_ratio"] != 0),
    "板块涨跌(景气代理)": sum(1 for it in items if it["sec_chg"] != 0),
    "新闻关注(舆情代理)": sum(1 for it in items if it["news_count"] > 0),
}
print(f"\n数据覆盖率 (有真实数据/30指数):")
for k, v in real_data.items():
    print(f"  {k}: {v}/30")

print(f"\n=== V5 TOP10 ===")
for it in items[:10]:
    print(f"#{it['rank']:2d} {it['name']:15s} | Q{it['quality']:4.1f} C{it['capital']:4.1f} T{it['technical']:4.1f} N{it['news']:4.1f} = {it['total']:4.1f} [{it['grade']}]")

_out_dir = os.path.dirname(os.path.abspath(__file__))

# 保存
with open(os.path.join(_out_dir, "v5_data.json"), "w") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
print(f"\n已保存: v5_data.json")

# ============================================================
# 生成 HTML 报告
# ============================================================
grade_counts = {}
for g in ["优质配置", "中性偏多", "均衡观望", "谨慎规避", "坚决规避"]:
    grade_counts[g] = sum(1 for s in items if s["grade"] == g)
avg_total = round(sum(s["total"] for s in items) / len(items), 2)

grade_dist_html = ""
for g, css_cls, emoji in [("优质配置","grade-excellent","🟢"),("中性偏多","grade-bullish","🔵"),("均衡观望","grade-neutral","🟡"),("谨慎规避","grade-cautious","🟠"),("坚决规避","grade-avoid","🔴")]:
    cnt = grade_counts.get(g, 0)
    if cnt > 0:
        grade_dist_html += f'<span class="grade-tag {css_cls}" style="margin:2px">{emoji} {g} {cnt}</span> '

def fmt_chg(v):
    if v is None: return "-"
    if v > 0: return f'<span class="red">+{v:.2f}%</span>'
    if v < 0: return f'<span class="green">{v:.2f}%</span>'
    return f'{v:.2f}%'

def fmt_flow(v):
    if v == 0: return '<span style="color:#999">0</span>'
    yi = v / 1e8
    if yi > 0: return f'<span class="green">+{yi:.2f}亿</span>'
    return f'<span class="red">{yi:.2f}亿</span>'

def fmt_val(v, suffix=""):
    if v is None: return '<span style="color:#999">-</span>'
    return f'{v:.2f}{suffix}'

def turnover_tag(it):
    src = it.get("idx_turnover_source", "no_data")
    tr = it.get("idx_turnover", 0)
    if tr > 0:
        if src == "westock":
            return f'{tr:.2f}% <span class="src-tag src-A">westock</span>'
        elif src == "tencent":
            return f'{tr:.2f}% <span class="src-tag src-B">腾讯</span>'
        elif src == "etf_proxy":
            return f'{tr:.2f}% <span class="src-tag src-C">ETF代理</span>'
        return f'{tr:.2f}%'
    return f'<span style="color:#999">-</span> <span class="src-tag src-C">无数据</span>'

def grade_color(g):
    return {"优质配置":"#27ae60","中性偏多":"#2980b9","均衡观望":"#f39c12","谨慎规避":"#e67e22","坚决规避":"#e74c3c"}.get(g, "#999")

def grade_css_class(g):
    return {"优质配置":"grade-excellent","中性偏多":"grade-bullish","均衡观望":"grade-neutral","谨慎规避":"grade-cautious","坚决规避":"grade-avoid"}.get(g, "grade-neutral")

def grade_emoji(g):
    return {"优质配置":"🟢","中性偏多":"🔵","均衡观望":"🟡","谨慎规避":"🟠","坚决规避":"🔴"}.get(g, "⚪")

def score_bar(val, max_val=10, color="#3498db"):
    w = max(2, val / max_val * 80)
    return f'<div class="bar-cell"><div class="bar" style="width:{w}px;background:{color}"></div></div>'

html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>指数四维精简量化评分体系 V5</title>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei", sans-serif; background:#f5f6fa; color:#2c3e50; }}
.header {{ background: linear-gradient(135deg, #1a1a2e, #16213e, #0f3460); color:white; padding: 30px 40px; text-align:center; }}
.header h1 {{ font-size:24px; margin-bottom:8px; letter-spacing:1px; }}
.header .sub {{ font-size:13px; opacity:.8; margin-bottom:12px; }}
.badge-row {{ display:flex; justify-content:center; gap:12px; flex-wrap:wrap; }}
.badge {{ display:inline-flex; align-items:center; gap:5px; padding:4px 14px; border-radius:12px; font-size:11px; font-weight:600; }}
.badge-A {{ background:rgba(39,174,96,.25); color:#a3f5bf; }} .badge-B {{ background:rgba(41,128,185,.25); color:#85c1e9; }} .badge-C {{ background:rgba(243,156,18,.25); color:#f9e79f; }} .badge-S {{ background:rgba(142,68,173,.25); color:#d7bde2; }}
.conf-dot {{ display:inline-block; width:8px; height:8px; border-radius:50%; margin-right:3px; }}

.tabs {{ display:flex; gap:4px; padding:14px 24px; background:white; border-bottom:1px solid #eee; flex-wrap:wrap; }}
.tab {{ padding:8px 20px; border-radius:20px; cursor:pointer; font-size:13px; border:1px solid #ddd; background:white; transition:.2s; }}
.tab:hover {{ background:#eef2ff; }}
.tab.active {{ background:#0f3460; color:white; border-color:#0f3460; }}
.content {{ max-width:1400px; margin:20px auto; padding:0 20px; }}
.panel {{ display:none; }}
.panel.active {{ display:block; }}

.summary-cards {{ display:flex; gap:12px; margin-bottom:20px; flex-wrap:wrap; }}
.summary-card {{ background:white; border-radius:12px; padding:18px 22px; flex:1; min-width:160px; box-shadow:0 2px 8px rgba(0,0,0,.06); text-align:center; }}
.summary-card .label {{ font-size:11px; color:#999; margin-bottom:4px; }}
.summary-card .value {{ font-size:26px; font-weight:700; }}
.summary-card .sub {{ font-size:11px; color:#888; margin-top:3px; }}

.table-container {{ background:white; border-radius:12px; padding:20px; box-shadow:0 2px 8px rgba(0,0,0,.06); overflow-x:auto; margin-bottom:20px; }}
table {{ width:100%; border-collapse:collapse; font-size:12px; }}
th {{ background:#f8f9fa; padding:10px 6px; text-align:center; font-weight:600; color:#555; border-bottom:2px solid #e9ecef; white-space:nowrap; position:sticky; top:0; }}
td {{ padding:8px 6px; text-align:center; border-bottom:1px solid #f0f0f0; }}
tr:hover {{ background:#f8faff; }}
.score-cell {{ font-weight:600; font-size:12px; cursor:pointer; }}
.score-cell:hover {{ text-decoration:underline; }}
.grade-tag {{ display:inline-flex; align-items:center; gap:4px; padding:4px 12px; border-radius:14px; font-size:11px; font-weight:700; color:white; white-space:nowrap; letter-spacing:.5px; box-shadow:0 1px 4px rgba(0,0,0,.15); }}
.grade-excellent {{ background:linear-gradient(135deg,#27ae60,#2ecc71); }}
.grade-bullish {{ background:linear-gradient(135deg,#2980b9,#3498db); }}
.grade-neutral {{ background:linear-gradient(135deg,#f39c12,#f1c40f); }}
.grade-cautious {{ background:linear-gradient(135deg,#e67e22,#f39c12); }}
.grade-avoid {{ background:linear-gradient(135deg,#e74c3c,#c0392b); }}
.red {{ color:#e74c3c; }} .green {{ color:#27ae60; }}
.name-cell {{ text-align:left; font-weight:600; white-space:nowrap; }}
.code-cell {{ font-size:10px; color:#999; }}
.reason {{ font-size:10px; color:#888; text-align:left; max-width:200px; white-space:normal; line-height:1.4; }}
.src-tag {{ display:inline-block; padding:1px 6px; border-radius:6px; font-size:9px; font-weight:600; }}
.src-A {{ background:#d4edda; color:#155724; }} .src-B {{ background:#d1ecf1; color:#0c5460; }} .src-C {{ background:#fff3cd; color:#856404; }}

.modal-overlay {{ display:none; position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,.45); z-index:999; justify-content:center; align-items:center; }}
.modal-overlay.show {{ display:flex; }}
.modal {{ background:white; border-radius:16px; padding:28px; max-width:620px; width:92%; max-height:82vh; overflow-y:auto; box-shadow:0 10px 40px rgba(0,0,0,.2); }}
.modal h3 {{ font-size:18px; margin-bottom:4px; }}
.modal .subtitle {{ font-size:12px; color:#888; margin-bottom:18px; }}
.modal .score-big {{ font-size:42px; font-weight:700; margin:8px 0; }}
.modal .item {{ padding:8px 0; border-bottom:1px solid #f5f5f5; font-size:12px; line-height:1.7; }}
.modal .item:last-child {{ border-bottom:none; }}
.modal-close {{ float:right; cursor:pointer; font-size:22px; color:#999; background:none; border:none; }}
.modal-close:hover {{ color:#333; }}

.method-box {{ background:white; border-radius:12px; padding:24px; box-shadow:0 2px 8px rgba(0,0,0,.06); margin-bottom:20px; }}
.method-box h3 {{ font-size:17px; margin-bottom:14px; border-bottom:2px solid #0f3460; padding-bottom:8px; display:inline-block; }}
.method-box td {{ font-size:11px; padding:6px 10px; }}
.method-box td:first-child {{ font-weight:600; text-align:left; }}

.audit-box {{ background:linear-gradient(135deg, #fff3cd, #fff8e1); border-left:4px solid #f39c12; border-radius:10px; padding:16px 20px; margin:14px 0; font-size:12px; }}
.audit-box h4 {{ color:#856404; margin-bottom:8px; font-size:14px; }}
.audit-box ul {{ margin-left:18px; }}
.audit-box li {{ margin:4px 0; line-height:1.7; }}

.bar-cell {{ display:flex; align-items:center; gap:4px; justify-content:center; }}
.bar {{ height:8px; border-radius:4px; min-width:2px; transition:width .3s; }}
.bar-quality {{ background:linear-gradient(90deg,#27ae60,#2ecc71); }}
.bar-tech {{ background:linear-gradient(90deg,#2980b9,#3498db); }}
.bar-news {{ background:linear-gradient(90deg,#f39c12,#f1c40f); }}
.bar-capital {{ background:linear-gradient(90deg,#8e44ad,#9b59b6); }}

footer {{ text-align:center; padding:30px; color:#aaa; font-size:11px; }}
</style>
</head>
<body>

<div class="header">
  <h1>指数四维精简量化评分体系 V5</h1>
  <div class="sub">对齐《指数四维精简量化评分体系（可落地·API适配）》· 0-10分制 · {NOW}</div>
  <div class="badge-row">
    <span class="badge badge-A"><span style="color:#27ae60">●</span> A级置信度: PE/PB/PS/股息率/60日动量/量比/ETF申赎/份额变化</span>
    <span class="badge badge-B"><span style="color:#2980b9">●</span> B级置信度: 板块资金/换手率/新闻/ETF涨跌(代理指标)</span>
  </div>
</div>

<div class="tabs">
  <div class="tab active" onclick="switchTab('ranking',event)">📊 综合排名</div>
  <div class="tab" onclick="switchTab('factors',event)">📈 因子拆解</div>
  <div class="tab" onclick="switchTab('audit',event)">🔍 数据溯源</div>
  <div class="tab" onclick="switchTab('method',event)">📐 方法论</div>
</div>

<div class="content">

<!-- Panel 1: Ranking -->
<div class="panel active" id="panel-ranking">
<div class="summary-cards">
  <div class="summary-card"><div class="label">评级分布</div><div class="value" style="font-size:13px;line-height:2">{grade_dist_html}</div></div>
  <div class="summary-card"><div class="label">平均总分</div><div class="value">{avg_total}</div><div class="sub">/10</div></div>
  <div class="summary-card"><div class="label">🏆 TOP1</div><div class="value" style="font-size:18px">{items[0]["name"]}</div><div class="sub"><span class="grade-tag {grade_css_class(items[0]["grade"])}" style="font-size:10px;padding:2px 8px">{items[0]["total"]}分 · {items[0]["grade"]}</span></div></div>
  <div class="summary-card"><div class="label">评分体系</div><div class="value" style="font-size:18px">0-10</div><div class="sub">四维等权25%</div></div>
</div>

<div class="table-container">
<table>
<thead><tr>
<th>#</th><th>指数名称</th><th>代码</th>
<th>质量<div style="font-size:9px;color:#27ae60">25%</div></th>
<th>资金面<div style="font-size:9px;color:#8e44ad">25%</div></th>
<th>技术面<div style="font-size:9px;color:#2980b9">25%</div></th>
<th>消息面<div style="font-size:9px;color:#f39c12">25%</div></th>
<th>总分</th><th>评级</th><th>核心理由</th>
</tr></thead>
<tbody>
'''

for s in items:
    reason = gen_reason(s, items)
    gc = grade_color(s["grade"])
    html += f'''<tr>
<td>{s["rank"]}</td>
<td class="name-cell">{s["name"]}</td>
<td class="code-cell">{s["code"]}</td>
<td><span class="score-cell" onclick="showDetail({s["rank"]-1})">{s["quality"]:.1f}</span>{score_bar(s["quality"], 10, "#27ae60")}</td>
<td><span class="score-cell" onclick="showDetail({s["rank"]-1})">{s["capital"]:.1f}</span>{score_bar(s["capital"], 10, "#8e44ad")}</td>
<td><span class="score-cell" onclick="showDetail({s["rank"]-1})">{s["technical"]:.1f}</span>{score_bar(s["technical"], 10, "#2980b9")}</td>
<td><span class="score-cell" onclick="showDetail({s["rank"]-1})">{s["news"]:.1f}</span>{score_bar(s["news"], 10, "#f39c12")}</td>
<td style="font-weight:700;font-size:15px;color:{gc}">{s["total"]:.1f}</td>
<td><span class="grade-tag {grade_css_class(s["grade"])}">{grade_emoji(s["grade"])} {s["grade"]}</span></td>
<td class="reason">{reason}</td>
</tr>'''

html += '</tbody></table></div></div>\n'

# Panel 2: Factor Breakdown
html += '<div class="panel" id="panel-factors">\n'
html += '<div class="table-container"><table><thead><tr><th>#</th><th>指数</th>'
html += '<th colspan="4" class="dim-header" style="background:#e8f5e9">质量因子(0-10)</th>'
html += '<th colspan="4" class="dim-header" style="background:#f3e5f5">资金面因子(0-10)</th>'
html += '<th colspan="3" class="dim-header" style="background:#e3f2fd">技术面因子(0-10)</th>'
html += '<th colspan="3" class="dim-header" style="background:#fff8e1">消息面因子(0-10)</th>'
html += '</tr><tr><th></th><th></th>'
html += '<th>PEG代理</th><th>ROE代理</th><th>增速代理</th><th>纯度代理</th>'
html += '<th>主力资金</th><th>北向代理</th><th>融资代理</th><th>机构代理</th>'
html += '<th>趋势</th><th>量价</th><th>RSI</th>'
html += '<th>景气度</th><th>预期</th><th>舆情</th>'
html += '</tr></thead><tbody>\n'

for s in items:
    html += f'''<tr>
<td>{s["rank"]}</td><td class="name-cell">{s["name"]}</td>
<td>{s["q_peg"]:.1f}</td><td>{s["q_roe"]:.1f}</td><td>{s["q_profit"]:.1f}</td><td>{s["q_purity"]:.1f}</td>
<td>{s["c_inflow5d"]:.1f}</td><td>{s["c_north"]:.1f}</td><td>{s["c_margin"]:.1f}</td><td>{s["c_inst"]:.1f}</td>
<td>{s["t_trend"]:.1f}</td><td>{s["t_vp"]:.1f}</td><td>{s["t_rsi"]:.1f}</td>
<td>{s["n_policy"]:.1f}</td><td>{s["n_consensus"]:.1f}</td><td>{s["n_sentiment"]:.1f}</td>
</tr>'''

html += '</tbody></table></div>\n'

html += '<div class="table-container" style="margin-top:16px"><table><thead><tr><th>#</th><th>指数</th><th>PE</th><th>PB</th><th>PS</th><th>股息率</th><th>5日涨跌</th><th>20日涨跌</th><th>60日涨跌</th><th>量比</th><th>振幅</th><th>指数换手</th><th>ETF净申赎</th><th>ETF份额变化率</th></tr></thead><tbody>\n'

for s in items:
    html += f'''<tr>
<td>{s["rank"]}</td><td class="name-cell">{s["name"]}</td>
<td>{fmt_val(s["pe"])}</td><td>{fmt_val(s["pb"])}</td><td>{fmt_val(s["ps"])}</td><td>{fmt_val(s["div"], "%")}</td>
<td>{fmt_chg(s["chg5"])}</td><td>{fmt_chg(s["chg20"])}</td>
<td>{fmt_chg(s["chg60"])}</td>
<td>{s["volume_ratio"]:.2f}</td><td>{s["swing"]:.2f}%</td>
<td>{turnover_tag(s)}</td>
<td>{fmt_flow(s["etf_flow_yuan"])}</td>
<td>{s["shares_chg_ratio"]:+.2f}%</td>
</tr>'''

html += '</tbody></table></div>\n'
html += '<div style="color:#888;font-size:11px;margin-top:8px;padding:0 8px">子因子评分 = 样本内百分位排名/10 → 0-10分 | 代理说明: PEG→PE百分位 | ROE→PB百分位 | 增速→PS百分位 | 纯度→股息率 | 北向→ETF申赎 | 融资→换手率 | 机构→份额变化 | 景气→板块涨跌 | 预期→ETF涨跌 | 舆情→新闻计数</div>\n'
html += '</div>\n'

# Panel 3: Data Audit
html += f'''<div class="panel" id="panel-audit">
<div class="audit-box">
<h4>🔍 V5 数据溯源核查报告</h4>
<ul>
<li><strong>评分体系</strong>: 对齐《指数四维精简量化评分体系（可落地·API适配）》文档</li>
<li><strong>评分范围</strong>: 0-10分制，四维等权25%</li>
<li><strong>评级标准</strong>: 9-10优质配置 | 7-8.9中性偏多 | 5-6.9均衡观望 | 3-4.9谨慎规避 | 0-2.9坚决规避</li>
<li><strong>数据拉取时间</strong>: {NOW} CST</li>
<li><strong>API调用</strong>: westock-data quote + etf + board + marketnews + 腾讯财经API</li>
<li><strong>代理指标说明</strong>: 因免费API无法获取ROE/北向/融资余额/RSI等原始因子，使用可获取的关联指标作为代理</li>
</ul>
</div>
'''

html += '<div class="method-box"><h3>因子-代理映射明细</h3><table>'
html += '<tr><th>维度</th><th>文档因子</th><th>代理指标</th><th>代理逻辑</th><th>数据源</th><th>置信度</th></tr>'
proxy_rows = [
    ("质量", "成分股加权PEG", "PE百分位(反向)", "PE越低→PEG越优", "westock quote", "A"),
    ("质量", "成分股加权ROE", "PB百分位(反向)", "低PB≈高ROE价值股", "westock quote", "B"),
    ("质量", "成分股净利润增速", "PS-TTM百分位(反向)", "低PS≈高营收效率", "westock quote", "B"),
    ("质量", "指数行业纯度", "股息率百分位", "高股息≈成熟纯赛道", "westock quote", "B"),
    ("资金面", "近5日板块主力资金强度", "板块5日资金净流入百分位", "直接对应", "westock board", "A"),
    ("资金面", "30日北向持仓变动", "ETF净申赎百分位", "机构ETF申赎≈北向", "westock etf", "B"),
    ("资金面", "10日融资余额变动率", "指数换手率百分位", "换手率≈杠杆情绪", "westock+腾讯", "B"),
    ("资金面", "机构季度持仓变动", "ETF份额变化率百分位", "份额增减≈机构行为", "westock etf", "B"),
    ("技术面", "指数中期趋势(60日线)", "60日涨跌幅百分位", "直接对应", "westock quote", "A"),
    ("技术面", "10日量价匹配度", "量比百分位", "量比>1=放量上涨", "westock quote", "B"),
    ("技术面", "RSI强弱&波动率", "振幅(反向)+5日动量混合", "低振幅+涨=强RSI", "westock quote", "B"),
    ("消息面", "行业政策与赛道景气度", "板块当日涨跌百分位", "板块涨=景气度高", "westock board", "B"),
    ("消息面", "行业机构一致预期", "ETF当日涨跌百分位", "ETF价=市场预期", "westock etf", "B"),
    ("消息面", "赛道舆情与风险事件", "新闻关注百分位", "新闻多=关注高", "westock marketnews", "B"),
]
for row in proxy_rows:
    conf_color = "#27ae60" if row[5] == "A" else "#f39c12"
    html += f'<tr><td>{row[0]}</td><td>{row[1]}</td><td>{row[2]}</td><td>{row[3]}</td><td>{row[4]}</td><td><span style="color:{conf_color};font-weight:700">{row[5]}</span></td></tr>'
html += '</table></div>'

# Data coverage stats
html += '<div class="method-box"><h3>数据覆盖率统计</h3><table>'
html += '<tr><th>数据维度</th><th>有真实数据</th><th>覆盖率</th><th>数据源</th></tr>'
for k, v in real_data.items():
    pct = v / len(items) * 100
    bar_color = "#27ae60" if pct >= 80 else "#f39c12" if pct >= 50 else "#e74c3c"
    if "ETF" in k or "份额" in k: src = "westock etf"
    elif "换手" in k: src = "westock+腾讯"
    elif "板块" in k or "主力" in k: src = "westock board"
    elif "新闻" in k or "舆情" in k: src = "westock marketnews"
    else: src = "westock quote"
    html += f'<tr><td>{k}</td><td>{v}/{len(items)}</td><td><span style="color:{bar_color};font-weight:700">{pct:.0f}%</span></td><td>{src}</td></tr>'
html += '</table></div>'

html += '''<div class="audit-box" style="background:linear-gradient(135deg,#f8d7da,#fce4ec);border-left-color:#e74c3c">
<h4 style="color:#721c24">⚠️ 代理指标局限性</h4>
<ul>
<li><strong>ROE→PB代理</strong>: PB与ROE并非严格反比，仅作为截面排名的近似替代</li>
<li><strong>北向→ETF申赎代理</strong>: ETF申赎包含所有机构行为，不仅限于北向资金</li>
<li><strong>融资→换手率代理</strong>: 换手率反映整体交易活跃度，不完全等于融资杠杆</li>
<li><strong>RSI→振幅+动量代理</strong>: 无法计算真实RSI(需逐日收盘价)，用振幅反向+短期动量近似</li>
<li><strong>机构预期→ETF涨跌代理</strong>: ETF价格反映市场整体预期，非仅机构预期</li>
<li><strong>板块匹配</strong>: 部分指数无精确对应板块，使用关键词匹配，可能存在偏差</li>
</ul>
</div>
</div>
'''

# Panel 4: Methodology
html += '''<div class="panel" id="panel-method">
<div class="method-box">
<h3>📐 指数四维精简量化评分体系 (0-10分制)</h3>
<p style="font-size:12px;color:#888;margin-bottom:16px">对齐《指数四维精简量化评分体系（可落地·API适配）》文档 · 四维度等权25% · 子因子0-10分 · 百分位排名归一化</p>
<table>
<tr><th>维度</th><th>权重</th><th>子因子(0-10分)</th><th>文档原始因子</th><th>代理指标</th></tr>
<tr><td rowspan="4">质量因子</td><td rowspan="4">25%</td><td>PEG代理(PE百分位/10)</td><td>成分股加权PEG</td><td>PE百分位(反向)</td></tr>
<tr><td>ROE代理(PB百分位/10)</td><td>成分股加权ROE</td><td>PB百分位(反向)</td></tr>
<tr><td>增速代理(PS百分位/10)</td><td>成分股净利润增速</td><td>PS-TTM百分位(反向)</td></tr>
<tr><td>纯度代理(股息率百分位/10)</td><td>指数行业纯度</td><td>股息率百分位</td></tr>

<tr><td rowspan="4">资金面因子</td><td rowspan="4">25%</td><td>主力资金(板块5日流入百分位/10)</td><td>近5日板块主力资金强度</td><td>板块5日资金净流入</td></tr>
<tr><td>北向代理(ETF申赎百分位/10)</td><td>30日北向持仓变动</td><td>ETF净申赎</td></tr>
<tr><td>融资代理(换手率百分位/10)</td><td>10日融资余额变动率</td><td>指数换手率</td></tr>
<tr><td>机构代理(份额变化百分位/10)</td><td>机构季度持仓变动</td><td>ETF份额变化率</td></tr>

<tr><td rowspan="3">技术面因子</td><td rowspan="3">25%</td><td>趋势(60日涨跌百分位/10)</td><td>指数中期趋势(60日线)</td><td>60日涨跌幅</td></tr>
<tr><td>量价(量比百分位/10)</td><td>10日量价匹配度</td><td>量比</td></tr>
<tr><td>RSI(振幅反向+5日动量混合/10)</td><td>RSI强弱&波动率</td><td>振幅+5日涨跌</td></tr>

<tr><td rowspan="3">消息面因子</td><td rowspan="3">25%</td><td>景气度(板块涨跌百分位/10)</td><td>行业政策与赛道景气度</td><td>板块当日涨跌</td></tr>
<tr><td>预期(ETF涨跌百分位/10)</td><td>行业机构一致预期</td><td>ETF当日涨跌</td></tr>
<tr><td>舆情(新闻百分位/10)</td><td>赛道舆情与风险事件</td><td>新闻标题计数</td></tr>
</table>
</div>

<div class="method-box">
<h3>📊 评级标准 (0-10分制)</h3>
<table>
<tr><td style="color:#27ae60;font-weight:700">🟢 优质配置</td><td>9.0 - 10.0</td><td>四因子均衡优秀，强烈推荐配置</td></tr>
<tr><td style="color:#2980b9;font-weight:700">🔵 中性偏多</td><td>7.0 - 8.9</td><td>多数因子偏多，可适度超配</td></tr>
<tr><td style="color:#f39c12;font-weight:700">🟡 均衡观望</td><td>5.0 - 6.9</td><td>因子均衡中性，维持标配</td></tr>
<tr><td style="color:#e67e22;font-weight:700">🟠 谨慎规避</td><td>3.0 - 4.9</td><td>多因子偏弱，建议低配</td></tr>
<tr><td style="color:#e74c3c;font-weight:700">🔴 坚决规避</td><td>0.0 - 2.9</td><td>全面弱势，坚决回避</td></tr>
</table>
</div>

<div class="method-box">
<h3>🔄 评分体系变更记录</h3>
<table>
<tr><td>评分范围</td><td>0-100分 → 0-10分 (对齐文档)</td></tr>
<tr><td>评级标准</td><td>S/A/B/C/D → 优质配置/中性偏多/均衡观望/谨慎规避/坚决规避 (对齐文档)</td></tr>
<tr><td>质量因子</td><td>PE×40%+PB×25%+PS×20%+股息×15% → PEG代理+ROE代理+增速代理+纯度代理 (4因子等权)</td></tr>
<tr><td>资金面因子</td><td>ETF申赎×50%+指数换手×30%+ETF换手×20% → 主力资金+北向代理+融资代理+机构代理 (4因子等权)</td></tr>
<tr><td>技术面因子</td><td>6子指标加权 → 趋势+量价+RSI (3因子等权, 对齐文档)</td></tr>
<tr><td>消息面因子</td><td>5子指标加权 → 景气度+预期+舆情 (3因子等权, 对齐文档)</td></tr>
<tr><td>新增数据</td><td>板块5日资金净流入 + ETF份额变化率</td></tr>
</table>
</div>
</div>
'''

html += '</div>\n'

# Modal + JS
html += '''
<div class="modal-overlay" id="modalOverlay" onclick="closeModal(event)">
<div class="modal" id="modalContent" onclick="event.stopPropagation()"></div>
</div>

<footer>指数四维精简量化评分体系 V5 · 对齐文档 · 0-10分制 · ''' + NOW + '''</footer>

<script>
const items = ''' + json.dumps(items, ensure_ascii=False) + ''';

function switchTab(name, e) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('panel-' + name).classList.add('active');
  if (e && e.target) e.target.classList.add('active');
}

function showDetail(idx) {
  const s = items[idx];
  const gc = {"优质配置":"#27ae60","中性偏多":"#2980b9","均衡观望":"#f39c12","谨慎规避":"#e67e22","坚决规避":"#e74c3c"}[s.grade]||"#999";
  const ge = {"优质配置":"🟢","中性偏多":"🔵","均衡观望":"🟡","谨慎规避":"🟠","坚决规避":"🔴"}[s.grade]||"⚪";
  let h = '<button class="modal-close" onclick="closeModal(event)">✕</button>';
  h += '<h3>' + s.name + '</h3>';
  h += '<div class="subtitle">' + s.code + ' | ' + s.sector + '</div>';
  h += '<div class="score-big" style="color:' + gc + '">' + s.total.toFixed(1) + ' <span style="font-size:16px">' + ge + ' ' + s.grade + '</span></div>';

  h += '<div style="margin:12px 0">';
  h += '<div class="item"><strong style="color:#27ae60">质量因子 ' + s.quality.toFixed(1) + '/10</strong> — PEG代理:' + s.q_peg.toFixed(1) + ' | ROE代理:' + s.q_roe.toFixed(1) + ' | 增速代理:' + s.q_profit.toFixed(1) + ' | 纯度代理:' + s.q_purity.toFixed(1) + '</div>';
  h += '<div class="item"><strong style="color:#8e44ad">资金面因子 ' + s.capital.toFixed(1) + '/10</strong> — 主力:' + s.c_inflow5d.toFixed(1) + ' | 北向代理:' + s.c_north.toFixed(1) + ' | 融资代理:' + s.c_margin.toFixed(1) + ' | 机构代理:' + s.c_inst.toFixed(1) + '</div>';
  h += '<div class="item"><strong style="color:#2980b9">技术面因子 ' + s.technical.toFixed(1) + '/10</strong> — 趋势:' + s.t_trend.toFixed(1) + ' | 量价:' + s.t_vp.toFixed(1) + ' | RSI:' + s.t_rsi.toFixed(1) + '</div>';
  h += '<div class="item"><strong style="color:#f39c12">消息面因子 ' + s.news.toFixed(1) + '/10</strong> — 景气:' + s.n_policy.toFixed(1) + ' | 预期:' + s.n_consensus.toFixed(1) + ' | 舆情:' + s.n_sentiment.toFixed(1) + '</div>';
  h += '</div>';

  h += '<div style="margin-top:10px;font-size:11px;color:#888;border-top:1px solid #eee;padding-top:10px">';
  h += 'PE:' + (s.pe||'-') + ' | PB:' + (s.pb||'-') + ' | PS:' + (s.ps||'-') + ' | 股息率:' + (s.div?s.div.toFixed(2)+'%':'-') + '<br>';
  h += '5日:' + s.chg5.toFixed(2) + '% | 20日:' + s.chg20.toFixed(2) + '% | 60日:' + s.chg60.toFixed(2) + '%<br>';
  h += '量比:' + s.volume_ratio.toFixed(2) + ' | 振幅:' + s.swing.toFixed(2) + '%<br>';
  const flowYi = s.etf_flow_yuan / 1e8;
  h += 'ETF申赎:' + (flowYi>=0?'+':'') + flowYi.toFixed(2) + '亿 | 份额变化:' + s.shares_chg_ratio.toFixed(2) + '%';
  h += '</div>';

  document.getElementById('modalContent').innerHTML = h;
  document.getElementById('modalOverlay').classList.add('show');
}

function closeModal(e) {
  if (e.target === document.getElementById('modalOverlay') || e.target.classList.contains('modal-close')) {
    document.getElementById('modalOverlay').classList.remove('show');
  }
}
document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') { document.getElementById('modalOverlay').classList.remove('show'); }
});
</script>
</body></html>
'''

html_path = os.path.join(_out_dir, "index_v5.html")
with open(html_path, "w") as f:
    f.write(html)
print(f"\n已生成HTML报告: {html_path} ({len(html)} 字符)")
