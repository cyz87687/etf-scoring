"""CI matrix 分片抓取脚本：抓取一个分片（默认 1 个指数）的日K线技术因子与行情。

背景：腾讯 web.ifzq K线接口对"同出口 IP 连续请求"仅首个能拿到K线数据(实测沙箱与
GitHub 均恰好 9/30，且成功的 9 个全是 000/399/hk，中证 cs 基本不返回)。workflow 用
matrix 把 30 个指数切成每片 1 个(CHUNK=1)，每个分片在独立 runner IP 上只发 1 个腾讯
K线请求；腾讯未拿到的(尤其中证/被限量排除的主板)，再用新浪 K线兜底(新浪覆盖 000/399
主板且无限流)。本脚本即单个分片的抓取器，输出 {原code: {技术因子+行情}}。
"""
import sys
import json

import fetch_data


def main():
    if len(sys.argv) < 3:
        print("用法: python fetch_klines.py <chunk.json> <out.json>")
        sys.exit(1)
    chunk_file, out_file = sys.argv[1], sys.argv[2]
    chunk = json.load(open(chunk_file, encoding="utf-8"))  # list of 原始指数code

    tx_to_code = {}
    tx_codes = []
    for c in chunk:
        tc = fetch_data._tx_code(c)
        if tc:
            tx_codes.append(tc)
            tx_to_code[tc] = c

    klines, quotes = fetch_data.fetch_tencent_klines(tx_codes)
    out = {}
    for tc, code in tx_to_code.items():
        rec = {}
        if klines.get(tc):
            rec.update(klines[tc])  # 技术因子（含 close）
        if quotes.get(tc):
            q = quotes[tc]
            rec["change_pct"] = q.get("change_pct", 0)
            rec["turnover_rate"] = q.get("turnover_rate", 0)
            rec["volume_ratio"] = q.get("volume_ratio", 0)
            rec["pe_ratio"] = q.get("pe_ratio")
            # 行情收盘价优先于 K线收盘价（K线可能滞后一日）
            if q.get("close") is not None:
                rec["close"] = q["close"]
        if rec:
            out[code] = rec
    # 新浪兜底: 腾讯常返回"有行情但无K线"的空壳(rec 有 change_pct 但无 rsi14),
    # 此时用新浪补全真实K线(新浪覆盖 000/399 主板, 无限流)。注意: 只要腾讯给了
    # 行情节点 out[code] 就非空, 故必须以"缺 rsi14(无K线技术因子)"判定, 而非"code 不在 out"。
    sina_missing = [c for c in chunk if out.get(c, {}).get("rsi14") is None]
    if sina_missing:
        sina = fetch_data.fetch_sina_klines(sina_missing)
        for c, tech in sina.items():
            if out.get(c, {}).get("rsi14") is None:
                out[c] = dict(tech)
    json.dump(out, open(out_file, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  分片({len(chunk)}码) -> 命中 {len(out)} 条, 写入 {out_file}")


if __name__ == "__main__":
    main()
