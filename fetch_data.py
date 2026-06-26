#!/usr/bin/env python3
"""
纯API数据获取模块 — 替代 westock-data CLI
用于 GitHub Actions 等无 westock-data 的环境
获取指数行情、ETF数据、板块数据、新闻数据
"""
import json
import math
import re
import time
import requests

# ============================================================
# 指数列表
# ============================================================
INDICES = [
    {"code": "399960", "name": "通信设备", "wcode": "sz399960"},
    {"code": "399614", "name": "中证全指电力指数", "wcode": "sz399614"},
    {"code": "399453", "name": "800能源", "wcode": "sz399453"},
    {"code": "399998", "name": "中证煤炭", "wcode": "sz399998"},
    {"code": "399006", "name": "创业板指", "wcode": "sz399006"},
    {"code": "000813", "name": "细分化工", "wcode": "sh000813"},
    {"code": "399986", "name": "中证旅游", "wcode": "sz399986"},
    {"code": "399970", "name": "消费电子", "wcode": "sz399970"},
    {"code": "000685", "name": "科创芯片", "wcode": "sh000685"},
    {"code": "399628", "name": "工业有色", "wcode": "sz399628"},
    {"code": "931865", "name": "中证半导体材料设备", "wcode": "sh931865"},
    {"code": "399967", "name": "中证军工", "wcode": "sz399967"},
    {"code": "930851", "name": "CS创新药", "wcode": "sh930851"},
    {"code": "932027", "name": "中证数据", "wcode": "sh932027"},
    {"code": "930598", "name": "稀土产业", "wcode": "sh930598"},
    {"code": "931624", "name": "中证信创", "wcode": "sh931624"},
    {"code": "H30184", "name": "半导体", "wcode": "shH30184"},
    {"code": "399971", "name": "动漫游戏", "wcode": "sz399971"},
    {"code": "932000", "name": "中证2000", "wcode": "sh932000"},
    {"code": "930850", "name": "港股创新药", "wcode": "sh930850"},
    {"code": "930982", "name": "港股通汽车", "wcode": "sh930982"},
    {"code": "931630", "name": "建筑材料", "wcode": "sh931630"},
    {"code": "930757", "name": "CS汽车", "wcode": "sh930757"},
    {"code": "931151", "name": "光伏产业", "wcode": "sh931151"},
    {"code": "931605", "name": "软件指数", "wcode": "sh931605"},
    {"code": "931755", "name": "CS电池", "wcode": "sh931755"},
    {"code": "930821", "name": "中证农业", "wcode": "sh930821"},
    {"code": "931641", "name": "通用航空", "wcode": "sh931641"},
    {"code": "399997", "name": "中证白酒", "wcode": "sz399997"},
    {"code": "931689", "name": "云计算", "wcode": "sh931689"},
    {"code": "931572", "name": "港股通科技", "wcode": "sh931572"},
    {"code": "930859", "name": "香港证券", "wcode": "sh930859"},
    {"code": "HSTECH", "name": "恒生科技", "wcode": "hkHSTECH"},
]

ETF_MAP = {
    "399960": "sz159995", "399614": "sh562350", "399453": "sz159612",
    "399998": "sz161725", "399006": "sz159915", "000813": "sz159870",
    "399986": "sz159766", "399970": "sz159732", "000685": "sh588890",
    "399628": "sz160620", "931865": "sh562820", "399967": "sz512680",
    "930851": "sz159858", "932027": "sz159527", "930598": "sz159713",
    "931624": "sz159540", "H30184": "sh512480", "399971": "sz159869",
    "932000": "sh563300", "930850": "sz159878", "930982": "sz159827",
    "931630": "sz159745", "930757": "sz159825", "931151": "sh515790",
    "931605": "sz159852", "931755": "sz159805", "930821": "sz159825",
    "931641": "sz159778", "399997": "sz161725", "931689": "sh516510",
    "931572": "sz159827", "930859": "sh513090", "HSTECH": "sh513180",
}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://gu.qq.com/',
}
SINA_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/',
}


def fetch_tencent_indices():
    """从腾讯财经API获取指数行情数据(PE/PB/PS/涨跌幅等)"""
    print("获取指数行情数据(腾讯财经API)...")
    data = {}
    batch = []
    batch_map = {}

    for idx in INDICES:
        code = idx["code"]
        name = idx["name"]
        wcode = idx["wcode"]
        if code == "HSTECH" or code.startswith("H"):
            data[wcode] = _default_quote()
            continue
        if code.startswith("399"):
            tc = f"sz{code}"
        elif code.startswith("000") or code.startswith("9"):
            tc = f"sh{code}"
        else:
            data[wcode] = _default_quote()
            continue
        batch.append(tc)
        batch_map[tc] = (wcode, name)

    for i in range(0, len(batch), 8):
        chunk = batch[i:i+8]
        query = ",".join(chunk)
        url = f"https://qt.gtimg.cn/q={query}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            for line in r.text.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                val = line.split("=", 1)[1].strip('"')
                parts = val.split("~")
                if len(parts) > 39 and parts[1]:
                    tcode = parts[2]
                    key = f"sh{tcode}" if f"sh{tcode}" in batch_map else f"sz{tcode}" if f"sz{tcode}" in batch_map else None
                    if not key:
                        continue
                    wcode, name = batch_map[key]
                    data[wcode] = {
                        "change_pct": float(parts[32]) if parts[32] else 0,
                        "turnover_rate": float(parts[38]) if parts[38] else 0,
                        "volume_ratio": float(parts[49]) if len(parts) > 49 and parts[49] else 0,
                        "range_pct": 0,
                        "pe_ratio": float(parts[39]) if parts[39] and float(parts[39]) > 0 else None,
                        "pb_ratio": None,
                        "ps_ttm": None,
                        "dividend_yield": None,
                        "chg_5d": 0,
                        "chg_20d": 0,
                        "chg_60d": 0,
                        "chg_ytd": 0,
                    }
        except Exception as e:
            print(f"  腾讯API批次请求失败: {e}")
        time.sleep(0.3)

    for tc in batch:
        if tc in batch_map and batch_map[tc][0] not in data:
            data[batch_map[tc][0]] = _default_quote()

    ok = sum(1 for v in data.values() if v.get("pe_ratio"))
    print(f"  腾讯指数行情: {ok}/{len(INDICES)} 有PE数据")
    return data


def fetch_sina_index_detail():
    """从新浪财经获取指数详细估值数据(PE/PB/PS/股息率/涨跌幅)"""
    print("获取指数估值数据(新浪财经API)...")
    data = {}
    for idx in INDICES:
        code = idx["code"]
        name = idx["name"]
        wcode = idx["wcode"]
        if code == "HSTECH" or code.startswith("H"):
            data[wcode] = _default_quote()
            continue
        if code.startswith("399"):
            sina_code = f"sz{code}"
        elif code.startswith("000") or code.startswith("9"):
            sina_code = f"sh{code}"
        else:
            data[wcode] = _default_quote()
            continue

        url = f"https://hq.sinajs.cn/list={sina_code}"
        try:
            r = requests.get(url, headers={**SINA_HEADERS, 'Referer': 'https://finance.sina.com.cn/'}, timeout=10)
            # Parse the response
            match = re.search(r'"([^"]+)"', r.text)
            if match:
                parts = match.group(1).split(",")
                if len(parts) >= 32:
                    data[wcode] = {
                        "change_pct": float(parts[3]) if parts[3] else 0,
                        "turnover_rate": 0,
                        "volume_ratio": 0,
                        "range_pct": 0,
                        "pe_ratio": None,
                        "pb_ratio": None,
                        "ps_ttm": None,
                        "dividend_yield": None,
                        "chg_5d": 0,
                        "chg_20d": 0,
                        "chg_60d": 0,
                        "chg_ytd": 0,
                    }
        except Exception:
            pass
        time.sleep(0.1)

    print(f"  新浪指数行情: {len(data)}/{len(INDICES)}")
    return data


def fetch_eastmoney_index_valuation():
    """从东方财富获取指数估值数据(PE/PB/PS/股息率/多期涨跌幅)"""
    print("获取指数估值数据(东方财富API)...")
    data = {}
    em_headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': 'https://data.eastmoney.com/',
    }

    for idx in INDICES:
        code = idx["code"]
        name = idx["name"]
        wcode = idx["wcode"]
        if code == "HSTECH" or code.startswith("H"):
            data[wcode] = _default_quote()
            continue

        # 东方财富指数代码转换
        if code.startswith("399"):
            secid = f"0.{code}"
        elif code.startswith("000") or code.startswith("9"):
            secid = f"1.{code}"
        else:
            data[wcode] = _default_quote()
            continue

        url = f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f9,f23,f20,f116,f117,f162,f167,f164,f168,f169,f170,f171,f277,f164,f173"
        try:
            r = requests.get(url, headers=em_headers, timeout=10)
            d = r.json().get("data", {})
            if d:
                pe = d.get("f9")
                pb = d.get("f23")
                ps = d.get("f20")
                div = d.get("f168")
                chg5 = d.get("f169")
                chg20 = d.get("f170")
                chg60 = d.get("f171")
                chg_ytd = d.get("f277")
                data[wcode] = {
                    "change_pct": d.get("f170", 0) or 0,
                    "turnover_rate": 0,
                    "volume_ratio": 0,
                    "range_pct": 0,
                    "pe_ratio": float(pe) if pe and float(pe) > 0 else None,
                    "pb_ratio": float(pb) if pb and float(pb) > 0 else None,
                    "ps_ttm": None,
                    "dividend_yield": float(div) if div and float(div) > 0 else None,
                    "chg_5d": float(chg5) if chg5 else 0,
                    "chg_20d": float(chg20) if chg20 else 0,
                    "chg_60d": float(chg60) if chg60 else 0,
                    "chg_ytd": float(chg_ytd) if chg_ytd else 0,
                }
        except Exception:
            pass
        time.sleep(0.1)

    ok = sum(1 for v in data.values() if v.get("pe_ratio"))
    print(f"  东方财富指数估值: {ok}/{len(INDICES)} 有PE数据")
    return data


def fetch_etf_data():
    """从腾讯财经API获取ETF数据(申赎/换手/涨跌)"""
    print("获取ETF数据(腾讯财经API)...")
    data = {}
    etf_codes = list(set(ETF_MAP.values()))
    batch = []

    for etf_code in etf_codes:
        if etf_code.startswith("sz"):
            batch.append(etf_code)
        elif etf_code.startswith("sh"):
            batch.append(etf_code)

    for i in range(0, len(batch), 8):
        chunk = batch[i:i+8]
        query = ",".join(chunk)
        url = f"https://qt.gtimg.cn/q={query}"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            for line in r.text.strip().split(";"):
                line = line.strip()
                if not line or "=" not in line:
                    continue
                val = line.split("=", 1)[1].strip('"')
                parts = val.split("~")
                if len(parts) > 40 and parts[1]:
                    tcode = parts[2]
                    name = parts[1]
                    close = float(parts[3]) if parts[3] else 0
                    chg_pct = float(parts[32]) if parts[32] else 0
                    turnover = float(parts[38]) if parts[38] else 0
                    nav = float(parts[36]) if parts[36] else close
                    # ETF份额和份额变化需要从其他API获取
                    data[f"sh{tcode}" if tcode.startswith("5") else f"sz{tcode}"] = {
                        "name": name,
                        "close": close,
                        "change_pct": chg_pct,
                        "turnover_rate": turnover,
                        "nav": nav,
                        "net_flow_yuan": 0,
                        "shares_chg_ratio": 0,
                    }
        except Exception as e:
            print(f"  ETF API批次请求失败: {e}")
        time.sleep(0.3)

    print(f"  ETF数据: {len(data)}/{len(etf_codes)}")
    return data


def fetch_sina_sectors():
    """从新浪财经获取行业板块涨跌数据"""
    print("获取行业板块数据(新浪财经API)...")
    sectors = {}
    try:
        url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
        r = requests.get(url, headers=SINA_HEADERS, timeout=10)
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


def fetch_news():
    """从腾讯财经获取市场新闻"""
    print("获取新闻数据(腾讯财经API)...")
    news_list = []
    try:
        url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newkline/news?market=hs&type=marketnews_hs"
        r = requests.get(url, headers=HEADERS, timeout=10)
        d = r.json()
        items = d.get("data", {}).get("news", [])
        for item in items[:30]:
            news_list.append({
                "time": item.get("time", ""),
                "title": item.get("title", ""),
                "symbol": item.get("symbol", ""),
            })
    except Exception as e:
        print(f"  新闻API失败: {e}")
    print(f"  新闻: {len(news_list)}条")
    return news_list


def _default_quote():
    return {
        "change_pct": 0, "turnover_rate": 0, "volume_ratio": 0, "range_pct": 0,
        "pe_ratio": None, "pb_ratio": None, "ps_ttm": None, "dividend_yield": None,
        "chg_5d": 0, "chg_20d": 0, "chg_60d": 0, "chg_ytd": 0,
    }


def fetch_all():
    """获取所有数据，返回与 build_v4.py 兼容的格式"""
    # 1. 指数行情 - 优先东方财富，回退腾讯
    em_quote = fetch_eastmoney_index_valuation()
    tx_quote = fetch_tencent_indices()

    # 合并：东方财富优先（有PE/PB等估值），腾讯补充换手率
    quote = {}
    for idx in INDICES:
        wcode = idx["wcode"]
        em = em_quote.get(wcode, _default_quote())
        tx = tx_quote.get(wcode, _default_quote())
        merged = {}
        for key in ["change_pct", "turnover_rate", "volume_ratio", "range_pct",
                     "pe_ratio", "pb_ratio", "ps_ttm", "dividend_yield",
                     "chg_5d", "chg_20d", "chg_60d", "chg_ytd"]:
            em_val = em.get(key)
            tx_val = tx.get(key)
            if key in ["pe_ratio", "pb_ratio", "ps_ttm", "dividend_yield"]:
                merged[key] = em_val if em_val else tx_val
            elif key == "turnover_rate":
                merged[key] = tx_val if tx_val and tx_val > 0 else em_val or 0
            else:
                merged[key] = em_val if em_val else tx_val or 0
        quote[wcode] = merged

    # 2. ETF数据
    etf_raw = fetch_etf_data()
    etf = {}
    for code, etf_code in ETF_MAP.items():
        e = etf_raw.get(etf_code, {})
        etf[etf_code] = e

    # 3. 板块数据
    board = fetch_sina_sectors()

    # 4. 新闻
    news = fetch_news()

    # 5. 热门股 (空列表，非核心)
    hot = []

    return quote, etf, board, news, hot


if __name__ == "__main__":
    quote, etf, board, news, hot = fetch_all()
    print(f"\n数据获取完成:")
    print(f"  指数行情: {len(quote)}")
    print(f"  ETF数据: {len(etf)}")
    print(f"  板块数据: {len(board)}")
    print(f"  新闻: {len(news)}")
