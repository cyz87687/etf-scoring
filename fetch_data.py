#!/usr/bin/env python3
"""
纯 API 数据获取模块 — 替代 westock-data CLI，用于 CI/CD 与无 westock 环境
设计目标（V6 重构）:
  1. 配置驱动: 由调用方传入指数 universe 与 ETF_MAP，消除与 build 的列表不一致
  2. 多源容错: 东方财富(估值/多期收益/ETF资金流) → 腾讯(行情/K线/ETF) → 新浪(板块)
     任一源失败仅降级该字段，不影响整体；全程记录覆盖率
  3. 真实技术指标: 从腾讯日K线计算 RSI(14)/年化波动率/60日动量/10日量价比/
     120日价格分位(估值位置代理)，对可获取指数的技术面不再依赖代理近似
  4. 真实 ETF 资金: 东方财富 ETF 资金流向 + 份额变化（可达时）
"""
import json
import math
import re
import time
import random
import statistics
import requests

TENCENT_H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://gu.qq.com/',
}

# 腾讯行情/ K线 共享同一 IP 限流预算；请求过快会被整体掐断。
# 用全局节流器让每次腾讯请求前都间隔 _TX_GAP 秒，保证单次运行稳定落在阈值内。
# 1.5s 已能规避绝大多数突发限流；指数 K 线另用更大间隔 + 退避重试（见 fetch_tencent_klines）。
_TX_GAP = 1.5
_TX_LAST = [0.0]
def _tget(url, timeout=10, max_retry=3):
    """带节流 + 重试的腾讯 GET；返回 (text, ok)"""
    for attempt in range(max_retry):
        wait = _TX_GAP - (time.time() - _TX_LAST[0])
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.get(url, headers=TENCENT_H, timeout=timeout)
            _TX_LAST[0] = time.time()
            if r.status_code == 200 and r.text.strip():
                return r.text, True
        except Exception:
            pass
        time.sleep(1.0 * (attempt + 1))
    return "", False
SINA_H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://finance.sina.com.cn/',
}
EM_H = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://quote.eastmoney.com/',
}

# 单指数行情字段默认值
def _default_quote():
    return {
        "close": None,                    # 收盘价（指数点位/价格），来自K线或行情
        "change_pct": 0, "turnover_rate": 0, "volume_ratio": 0, "range_pct": 0,
        "pe_ratio": None, "pb_ratio": None, "ps_ttm": None, "dividend_yield": None,
        "chg_5d": 0, "chg_20d": 0, "chg_60d": 0, "chg_ytd": 0, "chg_250d": 0,
        # 真实技术因子（来自K线，缺失时=None）
        "rsi14": None, "vol20": None, "mom60": None, "pct120": None, "vp10": None,
        "has_kline": False, "has_valuation": False,
    }


def _em_secids(code):
    """返回东方财富 secid 候选列表（按可能性排序）"""
    if code.startswith("399"):
        return ["0." + code]
    if code.startswith("000") or code[0] == "9":   # 上证 / 中证(9xxx)
        return ["1." + code, "0." + code]
    if code == "HSTECH" or code.startswith("H"):
        return ["100." + code, "116." + code]
    return ["1." + code]


def _tx_code(code):
    """返回腾讯代码。

    实测腾讯对中证指数(930/931/932 系列)使用 cs 前缀，如 cs931787=港股创新药、
    cs930598=稀土产业、cs932000=中证2000（cs 后缀格式无效）。该分支须置于通用
    '9' 分支之前，否则会被 sh 前缀错误拦截。
    """
    # 中证 930/931/932 系列: 腾讯用 cs 前缀（已实测可用）
    if code.startswith(("930", "931", "932")):
        return "cs" + code
    if code.startswith("399"):
        return "sz" + code
    if code.startswith("000") or (code[0] == "9" and not code.startswith("H")):
        return "sh" + code
    if code == "HSTECH":
        return "hkHSTECH"              # 恒生科技为港股，用 hk 前缀
    if code.startswith("H"):           # 其余 H 开头为国内中证指数(软件/工业有色/机器人/电力等)，用 cs 前缀
        return "cs" + code
    # 其余腾讯无覆盖
    return None


# ============================================================
# 1. 东方财富: 估值 + 多期收益 + ETF 资金流（可达时）
# ============================================================
def fetch_eastmoney_indices(indices):
    """估值(PE/PB/PS/股息) + 多期涨跌幅；返回 {wcode: {pe,pb,ps,div,chg5,20,60,ytd}}"""
    out = {}
    for idx in indices:
        code = idx["code"]; wcode = idx["wcode"]
        out[wcode] = {"pe_ratio": None, "pb_ratio": None, "ps_ttm": None,
                      "dividend_yield": None, "chg_5d": 0, "chg_20d": 0,
                      "chg_60d": 0, "chg_ytd": 0}
        for secid in _em_secids(code):
            url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
                   f"&fields=f9,f23,f20,f168,f169,f170,f171,f177")
            try:
                r = requests.get(url, headers=EM_H, timeout=8)
                d = r.json().get("data") or {}
                if not d:
                    continue
                pe = d.get("f9"); pb = d.get("f23"); ps = d.get("f20"); div = d.get("f168")
                out[wcode] = {
                    "pe_ratio": float(pe) if pe and float(pe) > 0 else None,
                    "pb_ratio": float(pb) if pb and float(pb) > 0 else None,
                    "ps_ttm": float(ps) if ps and float(ps) > 0 else None,
                    "dividend_yield": float(div) if div and float(div) > 0 else None,
                    "chg_5d": float(d.get("f169") or 0),
                    "chg_20d": float(d.get("f170") or 0),
                    "chg_60d": float(d.get("f171") or 0),
                    "chg_ytd": float(d.get("f177") or 0),
                }
                break
            except Exception:
                continue
    ok = sum(1 for v in out.values() if v["pe_ratio"])
    print(f"  东方财富估值: {ok}/{len(indices)} 有PE")
    return out


def fetch_eastmoney_etf_flow(etf_codes):
    """ETF 资金流向(主力净流入/份额) — 可达时填充"""
    out = {}
    for ec in etf_codes:
        prefix = "1." if ec.startswith("sh") else "0." if ec.startswith("sz") else None
        if not prefix:
            out[ec] = {"net_flow_yuan": 0, "shares_chg_ratio": 0}
            continue
        secid = prefix + ec[2:]
        url = (f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}"
               f"&fields=f62,f184,f85,f86")
        try:
            r = requests.get(url, headers=EM_H, timeout=8)
            d = r.json().get("data") or {}
            net = d.get("f62"); chg = d.get("f184")
            out[ec] = {
                "net_flow_yuan": float(net) if net else 0,
                "shares_chg_ratio": float(chg) if chg else 0,
            }
        except Exception:
            out[ec] = {"net_flow_yuan": 0, "shares_chg_ratio": 0}
    print(f"  东方财富ETF资金流: {sum(1 for v in out.values() if v['net_flow_yuan'])}/{len(etf_codes)}")
    return out


# ============================================================
# 2. 腾讯财经: 指数行情 + ETF行情
# ============================================================
def fetch_tencent_quotes(tx_codes, is_etf=False):
    """批量获取腾讯行情；返回 {tx_code: dict}。对整批做重试以抵抗限流。"""
    res = {}
    for i in range(0, len(tx_codes), 8):
        chunk = tx_codes[i:i + 8]
        url = "https://qt.gtimg.cn/q=" + ",".join(chunk)
        text, ok = _tget(url)
        if not ok or "none_match" in text:
            continue
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            val = line.split("=", 1)[1].strip('"')
            parts = val.split("~")
            if len(parts) <= 40 or not parts[1]:
                continue
            tcode = parts[2]
            key = ("sh" if tcode.startswith("5") or tcode.startswith("6") or tcode.startswith("9")
                   else "sz" if tcode.startswith("0") or tcode.startswith("3")
                   else "hk" if tcode.startswith(("H", "h")) else "") + tcode
            if key not in chunk:
                # 用原始前缀匹配（处理 cs 前缀等）
                key = next((c for c in chunk if c.endswith(tcode)), None)
            if not key:
                continue
            if is_etf:
                res[key] = {
                    "name": parts[1],
                    "close": float(parts[3]) if parts[3] else 0,
                    "change_pct": float(parts[32]) if parts[32] else 0,
                    "turnover_rate": float(parts[38]) if parts[38] else 0,
                    "nav": float(parts[36]) if parts[36] else 0,
                }
            else:
                res[key] = {
                    "close": float(parts[3]) if parts[3] else 0,   # 指数点位/收盘价
                    "change_pct": float(parts[32]) if parts[32] else 0,
                    "turnover_rate": float(parts[38]) if parts[38] else 0,
                    "volume_ratio": float(parts[49]) if len(parts) > 49 and parts[49] else 0,
                    "pe_ratio": float(parts[39]) if parts[39] and float(parts[39]) > 0 else None,
                }
    return res


# ============================================================
# 3. 腾讯 K线: 真实技术指标
# ============================================================
def compute_technicals(rows):
    """rows: [[date,open,close,high,low,vol], ...] → 技术指标 dict"""
    if not rows or len(rows) < 2:
        return None
    closes = [float(r[2]) for r in rows]
    highs = [float(r[3]) for r in rows]
    lows = [float(r[4]) for r in rows]
    vols = [float(r[5]) for r in rows]
    n = len(closes)
    cur = closes[-1]

    def pct_back(i):
        return (cur / closes[-1 - i] - 1) * 100 if i < n else None

    chg5 = pct_back(5) or 0
    chg20 = pct_back(20) or 0
    chg60 = pct_back(60) or 0
    chg_ytd = (cur / closes[0] - 1) * 100
    chg250 = pct_back(250) if n > 250 else chg_ytd

    mom60 = pct_back(60) or chg_ytd  # 60日动量

    # RSI(14)
    gains, losses = [], []
    for i in range(1, n):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    k = min(14, len(gains))
    ag = sum(gains[-k:]) / k if k else 0
    al = sum(losses[-k:]) / k if k else 0
    rsi14 = 100 - 100 / (1 + ag / al) if al > 0 else (100 if ag > 0 else 50)

    # 年化波动率(20日)
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, n)]
    m = min(20, len(rets))
    vol20 = statistics.pstdev(rets[-m:]) * math.sqrt(252) * 100 if m > 1 else 0

    # 120日价格分位（估值位置代理，越低越便宜）
    win = closes[-120:] if n >= 120 else closes
    lo, hi = min(win), max(win)
    pct120 = (cur - lo) / (hi - lo) * 100 if hi > lo else 50

    # 近20日平均振幅
    rng = [(highs[i] - lows[i]) / closes[i - 1] * 100 for i in range(1, n)]
    swing = sum(rng[-20:]) / min(20, len(rng)) if rng else 0

    # 10日量价配合: 上涨日成交量/下跌日成交量
    r10 = rets[-10:]; v10 = vols[-10:]
    up = sum(v for v, rr in zip(v10, r10) if rr > 0)
    dn = sum(v for v, rr in zip(v10, r10) if rr < 0)
    vp10 = (up / dn) if dn > 0 else (10.0 if up > 0 else 1.0)

    return {
        "close": round(cur, 4),          # 最新收盘价（指数点位）
        "chg_5d": chg5, "chg_20d": chg20, "chg_60d": chg60,
        "chg_ytd": chg_ytd, "chg_250d": chg250,
        "rsi14": round(rsi14, 2), "vol20": round(vol20, 2),
        "mom60": round(mom60, 2), "pct120": round(pct120, 2),
        "swing": round(swing, 3), "vp10": round(vp10, 3),
    }


def fetch_tencent_klines(tx_codes):
    """获取日K线并计算技术指标；返回 (klines, quotes)。

    - 腾讯对 web.ifzq.gtimg.cn 的 K 线接口有单 IP 额度（约 9 个/窗口），超限后返回
      200 但空/none_match。因此采用「分组 + 组间长冷却」错峰：每组最多 _KL_GROUP
      个请求，组间冷却 _KL_COOLDOWN 秒等待额度窗口重置，从而把 30 个指数全量抓取。
    - 单码仍带空响应递增退避重试，应对组内瞬时抖动。
    - cs 前缀的中证指数同样适用。
    - K线响应自带 qt 行情节点（与 qt.gtimg.cn 同源同格式），一并解析为
      指数行情，省去一次独立的指数行情请求，降低整体请求量。
    """
    out = {}
    qout = {}
    _KL_GAP = 3.0                       # 指数间基础间隔（秒）
    _KL_BACKOFF = [5, 10, 20]           # 空响应(疑似限流)时递增退避（秒）
    _KL_GROUP = 8                       # 每组请求数，规避腾讯单IP K线额度
    _KL_COOLDOWN = 120                  # 组间冷却（秒），等待额度窗口重置
    n = len(tx_codes)
    for i, code in enumerate(tx_codes):
        node = None
        for attempt in range(1 + len(_KL_BACKOFF)):
            # 基础间隔（与全局 _TX_GAP 取较大者，叠加生效）
            wait = _KL_GAP - (time.time() - _TX_LAST[0])
            if wait > 0:
                time.sleep(wait)
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,320,qfq"
            text, ok = _tget(url, timeout=10)
            if ok:
                try:
                    node = json.loads(text).get("data", {}).get(code, {})
                    kl = (node or {}).get("qfqday") or (node or {}).get("day") or []
                    # 关键: 必须真正拿到K线数据才算成功。被限流时响应常带空 node
                    # (或仅有 qt 行情节点而无 K线数组)，此时不能 break，需退避后重试。
                    if kl:
                        out[code] = compute_technicals(kl)
                        break
                except Exception:
                    node = None
            # 空响应 / 限流 → 递增退避后重试（带抖动避免同步）
            if attempt < len(_KL_BACKOFF):
                time.sleep(_KL_BACKOFF[attempt] + random.uniform(0, 1.0))
            else:
                break
        kl = (node or {}).get("qfqday") or (node or {}).get("day") or []
        if kl:
            out[code] = compute_technicals(kl)
        # 同一响应里的 qt 行情节点 → 指数行情（省一次独立请求）
        qt = (node or {}).get("qt", {}).get(code)
        if qt and len(qt) > 40 and qt[1]:
            qout[code] = {
                "close": float(qt[3]) if qt[3] else 0,
                "change_pct": float(qt[32]) if qt[32] else 0,
                "turnover_rate": float(qt[38]) if qt[38] else 0,
                "volume_ratio": float(qt[49]) if len(qt) > 49 and qt[49] else 0,
                "pe_ratio": float(qt[39]) if qt[39] and float(qt[39]) > 0 else None,
            }
        # 每组末尾冷却，规避腾讯单IP K线额度
        if (i + 1) % _KL_GROUP == 0 and (i + 1) < n:
            print(f"    K线分组冷却 {_KL_COOLDOWN}s ({i+1}/{n})")
            time.sleep(_KL_COOLDOWN)
    print(f"  腾讯K线技术指标: {sum(1 for v in out.values() if v)}/{n}")
    return out, qout


def fetch_em_klines(codes):
    """东方财富日K线兜底：补齐腾讯因单IP额度（约9个/运行）未取到的指数。

    返回 {原始指数code: compute_technicals结果}。腾讯能稳定拿到约9个，
    其余 ~21 个由东方财富补齐，从而把技术面覆盖拉到 30/30。
    """
    out = {}
    for code in codes:
        node = None
        for secid in _em_secids(code):
            url = ("https://push2his.eastmoney.com/api/qt/stock/kline/get"
                   "?fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57,f58"
                   "&ut=fa5fd1943c7b386f172d6893dbfba10b&klt=101&fqt=1"
                   f"&secid={secid}&beg=0&end=20500101&lmt=320")
            try:
                r = requests.get(url, headers=EM_H, timeout=10)
                d = r.json().get("data")
                if d and d.get("klines"):
                    node = d
                    break
            except Exception:
                pass
            time.sleep(0.4)
        if node and node.get("klines"):
            rows = []
            for kl in node["klines"]:
                p = kl.split(",")
                if len(p) >= 6:  # [date, open, close, high, low, volume, ...]
                    rows.append([p[0], p[1], p[2], p[3], p[4], p[5]])
            tech = compute_technicals(rows)
            if tech:
                out[code] = tech
        time.sleep(0.4)  # 礼貌间隔，避免触发东财限流
    print(f"  东方财富K线兜底: {len(out)}/{len(codes)}")
    return out


# ============================================================
# 4. 新浪行业板块 + 腾讯新闻
# ============================================================
def fetch_sina_sectors():
    sectors = {}
    try:
        url = 'https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php'
        r = requests.get(url, headers=SINA_H, timeout=10)
        for m in re.finditer(r'"([^"]+)":"([^"]+)"', r.text):
            parts = m.group(2).split(',')
            if len(parts) >= 5:
                name = parts[1]
                try:
                    chg = float(parts[4]) if parts[4] else 0
                except ValueError:
                    chg = 0
                sectors[name] = {"chg": chg, "chg_5d": 0, "inflow": 0, "inflow_5d": 0}
        print(f"  新浪行业板块: {len(sectors)}个")
    except Exception as e:
        print(f"  新浪行业板块失败: {e}")
    return sectors


def fetch_news():
    news = []
    try:
        url = "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newkline/news?market=hs&type=marketnews_hs"
        r = requests.get(url, headers=TENCENT_H, timeout=10)
        d = r.json() if r.text.strip().startswith("{") else {}
        items = d.get("data", {}).get("news", []) if isinstance(d.get("data"), dict) else []
        for it in items[:30]:
            if isinstance(it, dict):
                news.append({"time": it.get("time", ""), "title": it.get("title", ""), "symbol": it.get("symbol", "")})
    except Exception as e:
        print(f"  腾讯新闻失败: {e}")
    print(f"  新闻: {len(news)}条")
    return news


# ============================================================
# 主入口: 配置驱动，返回与 build 兼容的数据结构
# ============================================================
def fetch_all(indices, etf_map, sector_map=None, ext_klines=None):
    """
    参数:
      indices: [{"code","name","wcode",...}]  (来自 build 读取 Excel)
      etf_map: {index_code: etf_code}
    返回: (quote, etf, board, news, hot, coverage)
    """
    quote = {idx["wcode"]: _default_quote() for idx in indices}

    # --- 1. 东方财富估值（可达时）---
    em = fetch_eastmoney_indices(indices)
    for idx in indices:
        wcode = idx["wcode"]
        e = em.get(wcode, {})
        if e.get("pe_ratio"):
            quote[wcode].update({k: e[k] for k in ("pe_ratio", "pb_ratio", "ps_ttm",
                                                   "dividend_yield", "chg_5d", "chg_20d",
                                                   "chg_60d", "chg_ytd")})
            quote[wcode]["has_valuation"] = True

    # --- 2. 指数行情 + K线 ---
    idx_tx_codes = []
    tx_to_wcode = {}
    for idx in indices:
        tc = _tx_code(idx["code"])
        if tc:
            idx_tx_codes.append(tc)
            tx_to_wcode[tc] = idx["wcode"]

    if ext_klines:
        # CI matrix 分片抓取结果（已绕过腾讯单IP额度）: {原code: {技术因子+行情}}
        for idx in indices:
            rec = ext_klines.get(idx["code"])
            if not rec:
                continue
            wcode = idx["wcode"]
            if rec.get("change_pct") is not None:
                quote[wcode]["change_pct"] = rec.get("change_pct", 0)
            if rec.get("close") is not None:
                quote[wcode]["close"] = rec["close"]
            if rec.get("turnover_rate"):
                quote[wcode]["turnover_rate"] = rec.get("turnover_rate", 0)
            if rec.get("volume_ratio"):
                quote[wcode]["volume_ratio"] = rec.get("volume_ratio", 0)
            if rec.get("pe_ratio") and not quote[wcode]["pe_ratio"]:
                quote[wcode]["pe_ratio"] = rec["pe_ratio"]
                quote[wcode]["has_valuation"] = True
            if rec.get("rsi14") is not None or rec.get("pct120") is not None:
                quote[wcode].update({k: rec[k] for k in
                    ("chg_5d", "chg_20d", "chg_60d", "chg_ytd", "chg_250d",
                     "rsi14", "vol20", "mom60", "pct120", "swing", "vp10") if k in rec})
                quote[wcode]["has_kline"] = True
    else:
        # 一次 K线请求同时拿到行情(qt节点)与技术指标，省去独立指数行情请求
        tx_klines, tx_quotes = fetch_tencent_klines(idx_tx_codes)
        for tc, wcode in tx_to_wcode.items():
            q = tx_quotes.get(tc, {})
            if q.get("change_pct") is not None:
                quote[wcode]["change_pct"] = q["change_pct"]
            if q.get("turnover_rate"):
                quote[wcode]["turnover_rate"] = q["turnover_rate"]
            if q.get("volume_ratio"):
                quote[wcode]["volume_ratio"] = q["volume_ratio"]
            if q.get("pe_ratio") and not quote[wcode]["pe_ratio"]:
                quote[wcode]["pe_ratio"] = q["pe_ratio"]
                quote[wcode]["has_valuation"] = True
            if q.get("close") is not None:
                quote[wcode]["close"] = q["close"]
            k = tx_klines.get(tc)
            if k:
                quote[wcode].update(k)
                quote[wcode]["has_kline"] = True
                # K线多期收益优先（比东财更全）
                quote[wcode]["chg_5d"] = k["chg_5d"]
                quote[wcode]["chg_20d"] = k["chg_20d"]
                quote[wcode]["chg_60d"] = k["chg_60d"]
                quote[wcode]["chg_ytd"] = k["chg_ytd"]
                quote[wcode]["chg_250d"] = k["chg_250d"]

        # --- 2.5 东方财富 K线兜底：腾讯单IP额度仅~9个/运行，缺口用东财补齐 ---
        kline_wcodes = {tx_to_wcode[tc] for tc in tx_klines}
        missing = [idx for idx in indices if idx["wcode"] not in kline_wcodes]
        if missing:
            em_klines = fetch_em_klines([idx["code"] for idx in missing])
            code_to_wcode = {idx["code"]: idx["wcode"] for idx in missing}
            for code, k in em_klines.items():
                wcode = code_to_wcode.get(code)
                if wcode and k:
                    quote[wcode].update(k)
                    quote[wcode]["has_kline"] = True
                    quote[wcode]["chg_5d"] = k["chg_5d"]
                    quote[wcode]["chg_20d"] = k["chg_20d"]
                    quote[wcode]["chg_60d"] = k["chg_60d"]
                    quote[wcode]["chg_ytd"] = k["chg_ytd"]
                    quote[wcode]["chg_250d"] = k["chg_250d"]

    # --- 3. ETF 行情 + 资金流 ---
    etf_codes = list(dict.fromkeys(etf_map.values()))  # 去重保序
    etf_tx = fetch_tencent_quotes(etf_codes, is_etf=True)
    etf_flow = fetch_eastmoney_etf_flow(etf_codes)
    etf = {}
    for ec in etf_codes:
        e = etf_tx.get(ec, {})
        f = etf_flow.get(ec, {})
        etf[ec] = {
            "name": e.get("name", ""),
            "close": e.get("close", 0),
            "change_pct": e.get("change_pct", 0),
            "turnover_rate": e.get("turnover_rate", 0),
            "nav": e.get("nav", 0),
            "net_flow_yuan": f.get("net_flow_yuan", 0),
            "shares_chg_ratio": f.get("shares_chg_ratio", 0),
        }

    # --- 4. 板块 + 新闻 ---
    board = fetch_sina_sectors()
    news = fetch_news()
    hot = []

    # --- 5. 覆盖率统计 ---
    coverage = {
        "valuation": sum(1 for v in quote.values() if v["has_valuation"]),
        "kline": sum(1 for v in quote.values() if v["has_kline"]),
        "turnover": sum(1 for v in quote.values() if v["turnover_rate"] > 0),
        "volume_ratio": sum(1 for v in quote.values() if v["volume_ratio"] > 0),
        "chg60": sum(1 for v in quote.values() if v["chg_60d"] != 0),
        "etf": len(etf),
        "sectors": len(board),
        "news": len(news),
    }
    print(f"  覆盖率: 估值{coverage['valuation']} K线{coverage['kline']} "
          f"换手{coverage['turnover']} 60日动量{coverage['chg60']} 板块{coverage['sectors']} 新闻{coverage['news']}")
    return quote, etf, board, news, hot, coverage


if __name__ == "__main__":
    # 简易自测：用 Excel 配置
    import pandas as pd, os
    df = pd.read_excel(os.path.join(os.path.dirname(os.path.abspath(__file__)), "指数数据.xlsx"))
    INDICES = []
    for _, r in df.iterrows():
        raw = str(r.iloc[0]).strip(); name = str(r.iloc[1]).strip()
        code = raw.split(".")[0]
        INDICES.append({"code": code, "name": name, "wcode": code})
    q, e, b, n, h, cov = fetch_all(INDICES, {})
    print(json.dumps(cov, ensure_ascii=False))
