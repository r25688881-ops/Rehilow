"""個股反轉訊號排名：從 stocks_hist.json 挑成交值前 200 檔，輸出偏多/偏空各 10 檔到 stocks.json。
重用大盤回測的 14 個指標訊號（backtest.py 或 回測.py 皆可）。僅為統計排名，不構成投資建議。
"""
import datetime as dt
import json
import os

from common import ROOT, write_json

try:
    import backtest as bt
except ImportError:  # repo 內檔名若是 回測.py
    import importlib
    bt = importlib.import_module("回測")

TOPN, PICK, MIN_BARS = 200, 10, 70


def series(hist, code, dates):
    o, h, l, c, v, val = [], [], [], [], [], []
    for d in dates:
        r = hist["d"][d].get(code)
        if r:
            o.append(r[0]); h.append(r[1]); l.append(r[2]); c.append(r[3]); v.append(r[4]); val.append(r[5])
    return o, h, l, c, v, val


def run():
    with open(os.path.join(ROOT, "stocks_hist.json"), encoding="utf-8") as f:
        hist = json.load(f)
    dates = sorted(hist["d"])
    if len(dates) < MIN_BARS:
        raise SystemExit("資料不足，請先執行 fetch_stocks.py")
    last = dates[-1]
    recent = dates[-20:]
    turn = {}
    for code in hist["d"][last]:
        vals = [hist["d"][d][code][5] for d in recent if code in hist["d"][d]]
        if len(vals) >= 15:
            turn[code] = sum(vals) / len(vals)
    universe = sorted(turn, key=turn.get, reverse=True)[:TOPN]

    buys, sells = [], []
    for code in universe:
        o, h, l, c, v, val = series(hist, code, dates)
        if len(c) < MIN_BARS:
            continue
        S, snap = bt.build_signals(o, h, l, c)
        on = {k: bt.active(S[k])[-1] for k in bt.SIG_NAMES}
        nb = [k for k, x in on.items() if x and k.startswith("B_")]
        ns = [k for k, x in on.items() if x and k.startswith("S_")]
        vol_ratio = v[-1] / (sum(v[-21:-1]) / 20) if sum(v[-21:-1]) else 1.0
        chg = (c[-1] / c[-2] - 1) * 100
        item = {"code": code, "name": hist["names"].get(code, ""), "close": c[-1], "chg": round(chg, 2),
                "vol_ratio": round(vol_ratio, 2), "turnover": round(turn[code] / 1e8, 2),
                "rsi": round(snap["RSI14"], 1) if snap["RSI14"] is not None else None,
                "bias60": round(snap["季線乖離%"], 1) if snap["季線乖離%"] is not None else None}
        boost = 0.5 if vol_ratio >= 1.5 else 0
        if nb and len(nb) >= len(ns):
            sc = len(nb) + boost
            buys.append(dict(item, score=sc, buy=min(100, round(sc * 25)), reasons=[bt.SIG_NAMES[k] for k in nb]))
        if ns and len(ns) >= len(nb):
            sc = len(ns) + boost
            sells.append(dict(item, score=sc, sell=min(100, round(sc * 25)), reasons=[bt.SIG_NAMES[k] for k in ns]))
    key = lambda x: (x["score"], x["turnover"])
    buys = sorted(buys, key=key, reverse=True)[:PICK]
    sells = sorted(sells, key=key, reverse=True)[:PICK]
    write_json(os.path.join(ROOT, "stocks.json"), {
        "generated": dt.datetime.now().isoformat(timespec="seconds"), "date": last, "data_end": last, "universe": len(universe), "history_days": len(dates), "demo": False,
        "buy": buys, "sell": sells,
        "note": "訊號為近 5 日內觸發的反轉指標數量（成交量放大加 0.5 分），僅為統計排名，個別股票的歷史勝率未經驗證。"})
    print(f"[stocks] 母體 {len(universe)} 檔｜偏多 {len(buys)}｜偏空 {len(sells)}｜資料至 {last}")


if __name__ == "__main__":
    run()
