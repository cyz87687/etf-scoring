"""CI matrix 分片抓取脚本：抓取一个分片（≤8 个指数）的腾讯日K线技术因子与行情。

背景：腾讯 web.ifzq K线接口单 IP 硬上限约 9 个/运行。为在单次数据刷新中拿满 30 个
指数的技术面，workflow 用 matrix 把指数切成多个分片，每个分片在不同 runner IP 上
抓取（各自 ≤8，远低于上限），本脚本即单个分片的抓取器，输出 {原code: {技术因子+行情}}。
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
    json.dump(out, open(out_file, "w", encoding="utf-8"), ensure_ascii=False)
    print(f"  分片({len(chunk)}码) -> 命中 {len(out)} 条, 写入 {out_file}")


if __name__ == "__main__":
    main()
