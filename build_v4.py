#!/usr/bin/env python3
"""
V6 指数四维精简量化评分体系 (重构版)
- 对齐《指数四维精简量化评分体系（可落地·API适配）》四维度等权25%框架
- 关键改进:
  * 数据源统一由 Excel 单一来源驱动，消除 build/fetch 列表不一致(原致命bug)
  * 技术面改用 K线真实指标: RSI(14)/年化波动率/60日动量/10日量价比/120日价格分位
  * 质量面引入"120日价格分位"作为通用估值位置代理(覆盖所有可获取K线指数)
  * 新增数据置信度: 缺失因子不全给5分，按覆盖率标记并展示
  * 去除无效 log 变换与死代码(chg10)；新闻关键词增强
- 数据源: 东方财富(估值/ETF资金流) + 腾讯财经(行情/K线) + 新浪(板块)
"""
import json
import math
import os
import sys
from datetime import datetime
import pandas as pd
import fetch_data  # 数据层(单一来源驱动)

USE_API = "--api" in sys.argv
# CI matrix 分片抓取后合并的 K线结果文件(绕过腾讯单IP额度)
# 支持 --klines klines.json 与 --klines=klines.json 两种写法
KLINES_FILE = None
for _i, _a in enumerate(sys.argv):
    if _a == "--klines":
        if _i + 1 < len(sys.argv):
            KLINES_FILE = sys.argv[_i + 1]
            break
    elif _a.startswith("--klines="):
        KLINES_FILE = _a[len("--klines="):]
        break


def _load_klines():
    """加载 CI matrix 合并后的 K线结果 {原code: {技术因子+行情}}。"""
    if not KLINES_FILE:
        return None
    try:
        with open(KLINES_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] 加载 klines 文件失败({KLINES_FILE}): {e}")
        return None


NOW = datetime.now().strftime("%Y-%m-%d %H:%M")

# ============================================================
# 1. 读取 Excel 指数列表 (单一配置来源)
# ============================================================
_EXCEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "指数数据.xlsx")
df = pd.read_excel(_EXCEL)


def to_wcode(raw_code):
    """Excel 原始代码(如 931787.CSI) → 内部 wcode"""
    code = raw_code.split(".")[0].strip()
    if code == "HSTECH":
        return "hkHSTECH"
    if code.startswith("H") and code[1:].isdigit():
        return "cs" + code
    if code.isdigit() and code[0] == "9":
        return "cs" + code
    if code.isdigit() and code.startswith("000"):
        return "sh" + code
    if code.isdigit() and code.startswith("399"):
        return "sz" + code
    if code.isdigit():
        return "sh" + code
    return "cs" + code


INDICES = []
for _, r in df.iterrows():
    raw = str(r.iloc[0]).strip()
    name = str(r.iloc[1]).strip()
    code = raw.split(".")[0].strip()
    INDICES.append({"code": code, "name": name, "wcode": to_wcode(raw)})

# ETF 映射: 指数代码 → 跟踪ETF代码 (与 Excel 指数一一对应)
ETF_MAP = {
    "931787": "sh513120", "931151": "sh515790", "931743": "sh562590", "930598": "sh516780",
    "399967": "sh512660", "931855": "sh512670", "930709": "sh513090", "931719": "sh561910",
    "930902": "sh516000", "H30202": "sh515230", "399997": "sh512690", "931009": "sh516750",
    "000949": "sh516810", "H11059": "sz159871", "931247": "sh562570", "000685": "sh588200",
    "931239": "sh516800", "H30590": "sh562500", "932000": "sh563300", "000813": "sh516020",
    "H30199": "sh561700", "930633": "sh516100", "931160": "sh515880", "930851": "sh516510",
    "000928": "sz159930", "399998": "sh515220", "930901": "sz159869", "HSTECH": "sh513180",
}

# 指数 → 行业板块 (用于板块涨跌/资金匹配)
INDEX_TO_SECTOR = {
    "通信设备": "通信设备", "中证全指电力指数": "电力", "800能源": "石油行业", "中证煤炭": "煤炭",
    "创业板指": "创业板", "细分化工": "化学原料", "中证旅游": "旅游酒店", "消费电子": "消费电子",
    "科创芯片": "半导体", "工业有色": "小金属", "中证半导体材料设备": "半导体", "中证军工": "军工",
    "CS创新药": "医疗服务", "中证数据": "软件开发", "稀土产业": "小金属", "中证信创": "计算机设备",
    "半导体": "半导体", "动漫游戏": "游戏", "中证2000": "小盘", "港股创新药": "医疗服务",
    "港股通汽车": "汽车整车", "建筑材料": "建材", "CS汽车": "汽车整车", "光伏产业": "光伏设备",
    "软件指数": "软件开发", "CS电池": "电池", "中证农业": "种植业", "通用航空": "航空装备",
    "中证白酒": "白酒", "云计算": "云服务", "港股通科技": "互联网服务", "香港证券": "证券",
    "恒生科技": "互联网服务",
}


# ============================================================
# 2. 加载数据
# ============================================================
def _load_westock(paths):
    """legacy westock 文本解析 → 与 fetch_all 兼容的 (quote,etf,board,news,hot,coverage)"""
    import re
    quote = {idx["wcode"]: fetch_data._default_quote() for idx in INDICES}
    # quote
    try:
        with open(paths["quote"]) as f:
            for line in f:
                line = line.strip()
                if "| code |" in line:
                    start = True; continue
                if "---" in line:
                    continue
                if start and "|" in line:
                    p = [x.strip() for x in line.split("|")]
                    if len(p) < 42:
                        continue
                    wc = p[1]
                    if not wc.startswith(("cs", "sh", "sz", "hk")):
                        continue
                    q = quote.get(wc, fetch_data._default_quote())
                    try:
                        q.update({
                            "change_pct": float(p[16]) if p[16] else 0,
                            "volume_ratio": float(p[21]) if p[21] else 0,
                            "range_pct": float(p[22]) if p[22] else 0,
                            "pe_ratio": float(p[23]) if p[23] and float(p[23]) > 0 else None,
                            "pb_ratio": float(p[26]) if p[26] and float(p[26]) > 0 else None,
                            "ps_ttm": float(p[27]) if p[27] and float(p[27]) > 0 else None,
                            "dividend_yield": float(p[29]) if p[29] and float(p[29]) > 0 else None,
                            "chg_5d": float(p[36]) if p[36] else 0,
                            "chg_20d": float(p[38]) if p[38] else 0,
                            "chg_60d": float(p[39]) if p[39] else 0,
                            "chg_ytd": float(p[40]) if p[40] else 0,
                        })
                        if q["pe_ratio"]:
                            q["has_valuation"] = True
                        # 用chg60近似动量(无K线)
                        q["mom60"] = q["chg_60d"]
                        quote[wc] = q
                    except Exception:
                        pass
    except Exception:
        pass
    # etf / board / news 简化(legacy 路径主要保证可运行)
    etf, board, news, hot = {}, {}, [], []
    coverage = {"valuation": sum(1 for v in quote.values() if v["has_valuation"]),
                "kline": 0, "turnover": sum(1 for v in quote.values() if v["turnover_rate"] > 0),
                "volume_ratio": sum(1 for v in quote.values() if v["volume_ratio"] > 0),
                "chg60": sum(1 for v in quote.values() if v["chg_60d"] != 0),
                "etf": 0, "sectors": 0, "news": 0}
    return quote, etf, board, news, hot, coverage



print("=" * 60)
print(f"V6 指数四维精简量化评分体系 | {NOW}")
print("=" * 60)

if USE_API:
    print("\n[API模式] 使用纯API获取数据(配置来自 Excel)...")
    import fetch_data
    quote, etf, board, news, hot, coverage = fetch_data.fetch_all(INDICES, ETF_MAP, ext_klines=_load_klines() if KLINES_FILE else None)
else:
    # 本地 westock 模式: 读本地 westock 导出文本；缺失则自动回退 API
    import fetch_data
    _paths = {k: f"/tmp/v3_{k}.txt" for k in ("quote", "etf", "board", "news", "hot")}
    if os.path.exists(_paths["quote"]):
        quote, etf, board, news, hot, coverage = _load_westock(_paths)
    else:
        print("\n[回退] 未发现 westock 导出，自动切换 API 模式...")
        quote, etf, board, news, hot, coverage = fetch_data.fetch_all(INDICES, ETF_MAP, ext_klines=_load_klines() if KLINES_FILE else None)


# ============================================================
# 3. 评分辅助: 样本内百分位 + 置信度感知
# ============================================================
def pct_scores(values, inverted=False):
    """样本内百分位排名(0-100)。
    - None 视为缺失 → 赋中性值 50（不产生虚假区分）
    - 等值(含全0)取平均秩，避免人为拉开
    """
    n = len(values)
    idxs = [i for i, v in enumerate(values) if v is not None]
    if not idxs:
        return [50.0] * n
    vals = [values[i] for i in idxs]
    m = len(vals)
    order = sorted(range(m), key=lambda k: vals[k])
    raw = [0.0] * m
    i = 0
    while i < m:
        j = i
        while j + 1 < m and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg_rank = (i + j) / 2.0
        pct = (avg_rank / (m - 1) * 100) if m > 1 else 50.0
        for k in range(i, j + 1):
            raw[order[k]] = pct
        i = j + 1
    if inverted:
        raw = [100 - x for x in raw]
    out = [50.0] * n
    for pos, ii in enumerate(idxs):
        out[ii] = round(raw[pos], 1)
    return out


def pct_to_score(p):
    return round(p / 10, 2)


def rsi_to_score(rsi):
    """RSI(14) → 0-10: 40-70 稳健偏高(8-10)，<40递减，>75超买略降"""
    if rsi is None:
        return None
    if rsi <= 20:
        return 1.0
    if rsi < 40:
        return round(1 + (rsi - 20) / 20 * 4, 2)      # 20→1, 40→5
    if rsi <= 70:
        return round(5 + (rsi - 40) / 30 * 5, 2)      # 40→5, 70→10
    if rsi <= 80:
        return round(10 - (rsi - 70) / 10 * 2, 2)     # 70→10, 80→8
    return round(8 - (rsi - 80) / 20 * 4, 2)          # >80 降至4


def vol_to_score(vol):
    """年化波动率(%) → 0-10: 过低(缺乏活性)或过高(风险)均降分，10-25%最优"""
    if vol is None:
        return None
    if vol < 10:
        return round(4 + vol / 10 * 2, 2)             # <10 → 4-6
    if vol <= 25:
        return round(8 + (vol - 10) / 15 * 2, 2)      # 10-25 → 8-10
    if vol <= 40:
        return round(8 - (vol - 25) / 15 * 3, 2)      # 25-40 → 8-5
    return round(5 - min((vol - 40) / 20 * 3, 4), 2)  # >40 → 5→1


# ============================================================
# 4. 提取每个指数的原始字段 + 计算维度
# ============================================================
items = []
for idx in INDICES:
    wcode = idx["wcode"]; code = idx["code"]; name = idx["name"]
    etf_code = ETF_MAP.get(code, "")
    q = quote.get(wcode, fetch_data._default_quote())
    e = etf.get(etf_code, {})

    # 质量面原始值
    pe = q.get("pe_ratio"); pb = q.get("pb_ratio"); ps = q.get("ps_ttm"); div = q.get("dividend_yield")
    pct120 = q.get("pct120")            # 120日价格分位(估值位置代理)
    # 技术面原始值
    mom60 = q.get("mom60"); rsi14 = q.get("rsi14"); vol20 = q.get("vol20"); vp10 = q.get("vp10")
    chg5 = q.get("chg_5d", 0); chg20 = q.get("chg_20d", 0); chg60 = q.get("chg_60d", 0)
    chg_ytd = q.get("chg_ytd", 0); chg250 = q.get("chg_250d", 0)
    volume_ratio = q.get("volume_ratio", 0); swing = q.get("swing", 0)
    idx_turnover = q.get("turnover_rate", 0)
    # 资金面原始值
    etf_flow = e.get("net_flow_yuan", 0); shares_chg = e.get("shares_chg_ratio", 0)
    etf_chg = e.get("change_pct", 0); etf_turnover = e.get("turnover_rate", 0)
    # 板块匹配
    sector = INDEX_TO_SECTOR.get(name, "")
    sec = board.get(sector, {}) if sector else {}
    if not sec and sector:
        for bn, bd in board.items():
            if sector in bn or bn in sector:
                sec = bd; break
    sec_chg = sec.get("chg", 0) or q.get("change_pct", 0)
    sec_inflow_5d = sec.get("inflow_5d", 0) or sec.get("inflow", 0)
    # 新闻计数(增强关键词)
    news_count = 0
    if news and sector:
        for kw in [sector, name[:4], name[:3], name[:2]]:
            if len(kw) >= 2:
                news_count = sum(1 for nn in news if kw in nn.get("title", ""))
                if news_count:
                    break

    items.append({
        "name": name, "code": code, "wcode": wcode, "etf_code": etf_code,
        "etf_name": e.get("name", ""),
        "pe": pe, "pb": pb, "ps": ps, "div": div, "pct120": pct120,
        "chg5": chg5, "chg20": chg20, "chg60": chg60, "chg_ytd": chg_ytd, "chg250": chg250,
        "volume_ratio": volume_ratio, "swing": swing, "mom60": mom60, "rsi14": rsi14,
        "vol20": vol20, "vp10": vp10, "idx_turnover": idx_turnover,
        "etf_flow_yuan": etf_flow, "etf_turnover": etf_turnover, "etf_chg": etf_chg,
        "shares_chg_ratio": shares_chg, "sec_chg": sec_chg, "sec_inflow_5d": sec_inflow_5d,
        "news_count": news_count, "sector": sector, "has_kline": q.get("has_kline", False),
        "has_valuation": q.get("has_valuation", False),
    })

N = len(items)

# ---- 一、质量因子 (25%) ----
# 估值信号: PE(真实)/PB/PS/股息率(真实) + 120日价格分位(通用代理)
pe_pct = pct_scores([it["pe"] for it in items], inverted=True)
pb_pct = pct_scores([it["pb"] for it in items], inverted=True)
ps_pct = pct_scores([it["ps"] for it in items], inverted=True)
div_pct = pct_scores([it["div"] for it in items])
p120_pct = pct_scores([it["pct120"] for it in items], inverted=True)

for i, it in enumerate(items):
    subs = []
    if it["pe"] is not None:
        subs.append(pct_to_score(pe_pct[i]))
    if it["pb"] is not None:
        subs.append(pct_to_score(pb_pct[i]))
    if it["ps"] is not None:
        subs.append(pct_to_score(ps_pct[i]))
    if it["div"] is not None:
        subs.append(pct_to_score(div_pct[i]))
    if it["pct120"] is not None:        # 价格位置代理: 有K线即有
        subs.append(pct_to_score(p120_pct[i]))
    it["q_pe"], it["q_pb"], it["q_ps"], it["q_div"], it["q_pos"] = (
        pct_to_score(pe_pct[i]), pct_to_score(pb_pct[i]), pct_to_score(ps_pct[i]),
        pct_to_score(div_pct[i]), pct_to_score(p120_pct[i]))
    if not subs:
        it["quality"] = 5.0
    else:
        raw = sum(subs) / len(subs)
        # 子因子稀疏(仅1个)时向中性5收缩，避免单一指标极端化
        shrink = min(1.0, len(subs) / 3.0)
        it["quality"] = round(5.0 + (raw - 5.0) * shrink, 2)
    it["q_cov"] = round(len(subs) / 5, 2)

# ---- 二、资金面因子 (25%) ----
# 零值 = 缺失(无数据)，转为 None 以免被当成有效值排名
inflow5d_pct = pct_scores([it["sec_inflow_5d"] or None for it in items])
etf_flow_pct = pct_scores([it["etf_flow_yuan"] or None for it in items])
turn_pct = pct_scores([it["idx_turnover"] or None for it in items])
share_pct = pct_scores([it["shares_chg_ratio"] or None for it in items])

for i, it in enumerate(items):
    s_in = pct_to_score(inflow5d_pct[i])
    s_north = pct_to_score(etf_flow_pct[i])
    s_margin = pct_to_score(turn_pct[i])
    s_inst = pct_to_score(share_pct[i])
    it["c_inflow5d"], it["c_north"], it["c_margin"], it["c_inst"] = s_in, s_north, s_margin, s_inst
    real = sum([1 if it["sec_inflow_5d"] else 0,
                1 if it["etf_flow_yuan"] else 0,
                1 if it["idx_turnover"] else 0,
                1 if it["shares_chg_ratio"] else 0])
    it["capital"] = round((s_in + s_north + s_margin + s_inst) / 4, 2)
    it["c_cov"] = round(real / 4, 2)

# ---- 三、技术面因子 (25%) ----
mom_pct = pct_scores([it["mom60"] for it in items])
vp_pct = pct_scores([it["vp10"] for it in items])

for i, it in enumerate(items):
    s_trend = pct_to_score(mom_pct[i]) if it["mom60"] is not None else 5.0
    s_vp = pct_to_score(vp_pct[i]) if it["vp10"] is not None else 5.0
    s_rsi = rsi_to_score(it["rsi14"]) if it["rsi14"] is not None else 5.0
    s_vol = vol_to_score(it["vol20"]) if it["vol20"] is not None else 5.0
    it["t_trend"], it["t_vp"], it["t_rsi"], it["t_vol"] = s_trend, s_vp, s_rsi, s_vol
    tech_vals = [s_trend, s_vp, s_rsi, s_vol]
    real = sum(1 for x in (it["mom60"], it["vp10"], it["rsi14"], it["vol20"]) if x is not None)
    it["technical"] = round(sum(tech_vals) / 4, 2)
    it["t_cov"] = round(real / 4, 2)

# ---- 四、消息面因子 (25%) ----
sec_chg_pct = pct_scores([it["sec_chg"] or None for it in items])
etf_chg_pct = pct_scores([it["etf_chg"] or None for it in items])
news_pct = pct_scores([it["news_count"] or None for it in items])

for i, it in enumerate(items):
    s_policy = pct_to_score(sec_chg_pct[i])
    s_cons = pct_to_score(etf_chg_pct[i])
    s_sent = pct_to_score(news_pct[i])
    it["n_policy"], it["n_consensus"], it["n_sentiment"] = s_policy, s_cons, s_sent
    real = sum([1 if it["sec_chg"] else 0, 1 if it["etf_chg"] else 0, 1 if it["news_count"] else 0])
    it["news"] = round((s_policy + s_cons + s_sent) / 3, 2)
    it["n_cov"] = round(real / 3, 2)

# ---- 综合分 ----
for it in items:
    total = 0.25 * it["quality"] + 0.25 * it["capital"] + 0.25 * it["technical"] + 0.25 * it["news"]
    it["total"] = round(total, 2)
    # 综合数据置信度(0-1): 质量/技术/资金/消息覆盖均值
    it["confidence"] = round((it["q_cov"] + it["t_cov"] + it["c_cov"] + it["n_cov"]) / 4, 2)
    # 置信度折扣后的排序分: 低置信指数下修，避免"全中性5分"被误排高位
    penalty = max(0.0, 0.6 - it["confidence"]) / 0.6 * 2.0  # 置信<60%时最多扣2分
    it["adj_total"] = round(max(0.0, total - penalty), 2)
    # 评级基于原始分；但低置信额外标注
    if total >= 9.0:
        grade = "优质配置"
    elif total >= 7.0:
        grade = "中性偏多"
    elif total >= 5.0:
        grade = "均衡观望"
    else:
        grade = "谨慎规避"
    it["grade"] = grade
    it["conf_grade"] = "数据不足·仅供参考" if it["confidence"] < 0.35 else ""

items.sort(key=lambda x: -x["adj_total"])
for i, it in enumerate(items):
    it["rank"] = i + 1

# 覆盖率统计
real_data = {
    "PE(质量)": sum(1 for it in items if it["pe"] is not None),
    "PB(质量)": sum(1 for it in items if it["pb"] is not None),
    "PS(质量)": sum(1 for it in items if it["ps"] is not None),
    "股息率(质量)": sum(1 for it in items if it["div"] is not None),
    "120日价格分位(质量)": sum(1 for it in items if it["pct120"] is not None),
    "60日动量(技术)": sum(1 for it in items if it["mom60"] is not None),
    "RSI14(技术)": sum(1 for it in items if it["rsi14"] is not None),
    "年化波动率(技术)": sum(1 for it in items if it["vol20"] is not None),
    "10日量价比(技术)": sum(1 for it in items if it["vp10"] is not None),
    "指数换手率(资金)": sum(1 for it in items if it["idx_turnover"] > 0),
    "ETF资金流(资金)": sum(1 for it in items if it["etf_flow_yuan"] != 0),
    "ETF份额变化(资金)": sum(1 for it in items if it["shares_chg_ratio"] != 0),
    "板块涨跌(消息)": sum(1 for it in items if it["sec_chg"] != 0),
    "ETF涨跌(消息)": sum(1 for it in items if it["etf_chg"] != 0),
    "新闻关注(消息)": sum(1 for it in items if it["news_count"] > 0),
}
print(f"\n数据覆盖率 (有真实数据 / {N} 指数):")
for k, v in real_data.items():
    print(f"  {k}: {v}/{N}")
print(f"\n=== V6 TOP10 ===")
for it in items[:10]:
    print(f"#{it['rank']:2d} {it['name']:14s} | Q{it['quality']:4.1f} C{it['capital']:4.1f} "
          f"T{it['technical']:4.1f} N{it['news']:4.1f} = {it['total']:4.1f} [{it['grade']}] 置信{it['confidence']}")

# 保存 JSON
_out = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(_out, "v5_data.json"), "w") as f:
    json.dump(items, f, ensure_ascii=False, indent=2)
print("\n已保存: v5_data.json")

# 生成 HTML 报告
import build_report
build_report.save(items, NOW, real_data, _out)
