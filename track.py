"""長期追蹤與驗證：把每日『偏多/偏空』名單變成可驗證的紙上交易，輸出 track.json。

兩件事，互相獨立：
 A. 紙上交易（回答「照著做會賺多少」）
    - 收盤後出訊號，隔日『開盤價』成交（不偷看當日收盤）。含手續費 0.1425%×2 + 證交稅 0.3%。
    - 最多同時持有 MAX_POS 檔，等權；只做多（偏空名單用來當賣出訊號與反向驗證）。
    - 每日對持股給建議：買進 / 續買 / 抱 / 賣出。
 B. 前瞻記分卡（回答「建議準不準」）
    - 名單上每一檔，記下隔日開盤進場後 1/5/10/20 日的報酬，並扣掉同期『母體等權平均』得到超額報酬。
    - 偏多要「漲且贏過大盤」才算準；偏空要「跌」才算準。
    - 第一次執行會用歷史資料『重播』（標記為回補）；之後每天新增的才是『實盤前瞻』。兩者分開統計。

狀態全存在 track.json（要 commit），之後每天只增量處理新的交易日。
用法：python track.py        # 增量
      python track.py --reset # 砍掉重來
"""
import datetime as dt
import json
import os
import sys

from common import ROOT, write_json
import stock_signals as ss

try:
    import backtest as bt
except ImportError:
    import importlib
    bt = importlib.import_module("回測")

STATE = os.path.join(ROOT, "track.json")
MAX_POS = 10          # 同時最多持股檔數（每檔 1/MAX_POS 資金）
STOP = 0.08           # 停損：收盤跌破成本 8%
TRAIL = 0.10          # 移動停利：自持有期間最高收盤回落 10%
MAX_HOLD = 20         # 最長持有交易日
COOLDOWN = 5          # 賣出後 5 日內不再買回同一檔
FEE = 0.001425        # 買、賣各一次（未計券商折讓，偏保守）
TAX = 0.003           # 賣出證交稅
WINDOWS = (1, 5, 10, 20)
BM_MIN_VALUE = 1e8    # 母體基準：前一日成交值 ≥ 1 億的股票


def fresh():
    return {"version": 1, "params": {"max_pos": MAX_POS, "stop": STOP, "trail": TRAIL, "max_hold": MAX_HOLD,
                                      "fee": FEE, "tax": TAX, "cooldown": COOLDOWN},
            "last": None, "start": None, "pending": {"buy": [], "sell": []},
            "open": [], "trades": [], "picks": [], "bm": {}, "eq": [], "advice": None}


def load_state():
    if os.path.exists(STATE) and "--reset" not in sys.argv:
        with open(STATE, encoding="utf-8") as f:
            s = json.load(f)
        if s.get("version") == 1:
            return s
    return fresh()


# ---------- 基準：母體等權平均 ----------
def bm_day(hist, dates, i):
    """回傳 [開盤→收盤報酬, 昨收→收盤報酬]（等權、前一日成交值≥1億的股票）。"""
    d, p = hist["d"][dates[i]], hist["d"][dates[i - 1]]
    oc, cc = [], []
    for code, rp in p.items():
        r = d.get(code)
        if r is None or rp[5] < BM_MIN_VALUE or rp[3] <= 0 or r[0] <= 0:
            continue
        oc.append(r[3] / r[0] - 1)
        cc.append(r[3] / rp[3] - 1)
    if len(cc) < 20:
        return [0.0, 0.0]
    clip = lambda x: max(-0.11, min(0.11, x))
    return [round(sum(map(clip, oc)) / len(oc), 5), round(sum(map(clip, cc)) / len(cc), 5)]


def bm_window(st, dates, idx, n):
    """從 dates[idx] 開盤進場、持有到 dates[idx+n-1] 收盤的母體報酬。"""
    bm, v = st["bm"], 1.0
    for k in range(n):
        j = idx + k
        if dates[j] not in bm:
            return None
        v *= 1 + bm[dates[j]][0 if k == 0 else 1]
    return v - 1


# ---------- 交易可行性（漲跌停鎖死視為成交不了）----------
def locked(r, prev_close, up):
    o, h, l = r[0], r[1], r[2]
    if h != l or not prev_close:
        return False
    return o >= prev_close * 1.09 if up else o <= prev_close * 0.91


def advise(st, i, dates, buys, sells, closes):
    """收盤後決定：持股要賣/續買/抱；名單上沒持有的列為買進。回傳 (advice_rows, pend_sell, pend_buy)。"""
    bset = {b["code"] for b in buys}
    sset = {s["code"] for s in sells}
    rows, pend_sell = [], []
    for p in st["open"]:
        c = closes.get(p["code"], p["last_close"])
        ret = c * (1 - FEE - TAX) / (p["entry"] * (1 + FEE)) - 1
        act, why = "抱", "沒有出場訊號，續抱"
        if p["code"] in sset:
            act, why = "賣出", "偏空訊號出現"
        elif c <= p["entry"] * (1 - STOP):
            act, why = "賣出", f"停損（跌破成本 {STOP * 100:.0f}%）"
        elif p["peak"] > p["entry"] and c <= p["peak"] * (1 - TRAIL):
            act, why = "賣出", f"移動停利（自高點回落 {TRAIL * 100:.0f}%）"
        elif p["age"] >= MAX_HOLD:
            act, why = "賣出", f"持有滿 {MAX_HOLD} 個交易日"
        elif p["code"] in bset:
            act, why = "續買", "偏多訊號再度出現"
        if act == "賣出":
            pend_sell.append(p["code"])
        rows.append({"code": p["code"], "name": p["name"], "act": act, "why": why, "entry_date": p["entry_date"],
                     "entry": p["entry"], "close": c, "ret": round(ret, 4), "age": p["age"]})
    held = {p["code"] for p in st["open"]}
    free = MAX_POS - len(st["open"]) + len(pend_sell)
    exited = {t["code"]: t["exit_i"] for t in st["trades"][-60:]}
    pend_buy = []
    for b in buys:
        if b["code"] in held:
            continue
        cool = i - exited.get(b["code"], -999) < COOLDOWN
        if cool:
            rows.append({"code": b["code"], "name": b["name"], "act": "觀望", "why": f"近 {COOLDOWN} 日剛賣出，暫不買回",
                         "close": b["close"], "score": b["score"], "reasons": b["reasons"]})
        elif len(pend_buy) < free:
            pend_buy.append(b["code"])
            rows.append({"code": b["code"], "name": b["name"], "act": "買進", "why": "偏多訊號，明日開盤買進",
                         "close": b["close"], "score": b["score"], "reasons": b["reasons"]})
        else:
            rows.append({"code": b["code"], "name": b["name"], "act": "候補", "why": "持股已滿，暫列觀察",
                         "close": b["close"], "score": b["score"], "reasons": b["reasons"]})
    return rows, pend_sell, pend_buy


def process_day(st, hist, dates, i, live):
    D = dates[i]
    day, prev = hist["d"][D], hist["d"][dates[i - 1]]
    if i >= 1:
        st["bm"][D] = bm_day(hist, dates, i)
    r_day = 0.0

    # 1) 開盤：先執行昨天決定的賣出，再買進
    keep_sell = []
    for code in st["pending"]["sell"]:
        p = next((x for x in st["open"] if x["code"] == code), None)
        r = day.get(code)
        if p is None:
            continue
        if r is None or locked(r, prev.get(code, [0] * 6)[3], up=False):
            keep_sell.append(code)  # 停牌或跌停鎖死，延到下一天
            continue
        px = r[0]
        r_day += (px * (1 - FEE - TAX) / p["last_close"] - 1) / MAX_POS
        ret = px * (1 - FEE - TAX) / (p["entry"] * (1 + FEE)) - 1
        ei = dates.index(p["entry_date"]) if p["entry_date"] in dates else i - p["age"]
        bmv = bm_window(st, dates, ei, max(1, i - ei)) if i - ei >= 1 else None
        st["trades"].append({"code": code, "name": p["name"], "entry_date": p["entry_date"], "entry": p["entry"],
                             "exit_date": D, "exit": px, "exit_i": i, "ret": round(ret, 4), "days": i - ei,
                             "bm": None if bmv is None else round(bmv, 4), "why": p["why"], "live": p["live"]})
        st["open"].remove(p)
    st["pending"]["sell"] = keep_sell

    for code in st["pending"]["buy"]:
        r = day.get(code)
        if r is None or len(st["open"]) >= MAX_POS or any(x["code"] == code for x in st["open"]):
            continue
        if locked(r, prev.get(code, [0] * 6)[3], up=True):
            continue  # 漲停鎖死買不到
        px = r[0]
        r_day += (r[3] / (px * (1 + FEE)) - 1) / MAX_POS
        st["open"].append({"code": code, "name": hist["names"].get(code, ""), "entry_date": D, "entry": px,
                           "last_close": r[3], "peak": r[3], "age": 1, "why": "", "live": live})
    st["pending"]["buy"] = []

    # 2) 其他持股：收盤對收盤
    for p in st["open"]:
        r = day.get(p["code"])
        if r is None or p["entry_date"] == D:
            continue
        r_day += (r[3] / p["last_close"] - 1) / MAX_POS
        p["last_close"] = r[3]
        p["peak"] = max(p["peak"], r[3])
        p["age"] += 1

    # 3) 淨值（策略含閒置現金；基準為滿倉等權母體）
    if st["eq"]:
        _, e, b = st["eq"][-1]
        st["eq"].append([D, round(e * (1 + r_day), 5), round(b * (1 + st["bm"][D][1]), 5)])
    else:
        st["eq"].append([D, 1.0, 1.0])

    # 4) 收盤後：今天的名單 → 記錄、決定明天的動作
    buys, sells, _ = ss.pick(hist, dates, i)
    closes = {c: r[3] for c, r in day.items()}
    for side, lst in (("B", buys), ("S", sells)):
        for x in lst:
            st["picks"].append({"d": D, "s": side, "c": x["code"], "n": x["name"], "sc": x["score"], "id": x["ids"],
                                "live": live, "r": {}, "b": {}})
    rows, pend_sell, pend_buy = advise(st, i, dates, buys, sells, closes)
    why = {r_["code"]: r_["why"] for r_ in rows if r_["act"] == "賣出"}
    for p in st["open"]:
        p["why"] = why.get(p["code"], p["why"])
    st["pending"] = {"buy": pend_buy, "sell": pend_sell}
    st["advice"] = {"date": D, "rows": rows, "sell_list": [{"code": s["code"], "name": s["name"], "score": s["score"], "reasons": s["reasons"]} for s in sells]}
    st["last"] = D


# ---------- 前瞻報酬 ----------
def fill_forward(st, hist, dates):
    pos = {d: k for k, d in enumerate(dates)}
    for pk in st["picks"]:
        k = pos.get(pk["d"])
        if k is None:
            continue
        for n in WINDOWS:
            key = str(n)
            if key in pk["r"] or k + n >= len(dates):
                continue
            a, b = hist["d"][dates[k + 1]].get(pk["c"]), hist["d"][dates[k + n]].get(pk["c"])
            bw = bm_window(st, dates, k + 1, n)
            if a is None or b is None or a[0] <= 0 or bw is None:
                continue
            pk["r"][key] = round(b[3] / a[0] - 1, 4)
            pk["b"][key] = round(bw, 4)


def wilson_lb(k, n, z=1.64):
    return bt.wilson_lb(k, n, z) if n else 0.0


def score_card(picks, live):
    out = {}
    for side in ("B", "S"):
        out[side] = {}
        for n in WINDOWS:
            xs = [(p["r"][str(n)], p["b"][str(n)]) for p in picks if p["s"] == side and str(n) in p["r"] and (p["live"] if live else not p["live"])]
            if not xs:
                out[side][str(n)] = {"n": 0}
                continue
            m = len(xs)
            avg = sum(r for r, _ in xs) / m
            ex = sum(r - b for r, b in xs) / m
            if side == "B":   # 偏多：要漲，而且贏過母體
                hit = sum(1 for r, b in xs if r > 0 and r > b)
            else:             # 偏空：要跌
                hit = sum(1 for r, _ in xs if r < 0)
            out[side][str(n)] = {"n": m, "avg": round(avg, 4), "excess": round(ex, 4), "hit": hit, "rate": round(hit / m, 3), "lb": round(wilson_lb(hit, m), 3)}
    return out


def by_signal(picks, n=10):
    g = {}
    for p in picks:
        if str(n) not in p["r"]:
            continue
        for sid in p["id"]:
            g.setdefault(sid, []).append(p["r"][str(n)] - p["b"][str(n)])
    rows = [{"id": k, "n": len(v), "excess": round(sum(v) / len(v), 4)} for k, v in g.items() if len(v) >= 5]
    return sorted(rows, key=lambda x: -x["excess"])


def trade_stats(trades, live):
    ts = [t for t in trades if (t["live"] if live else not t["live"])]
    if not ts:
        return {"n": 0}
    wins = sum(1 for t in ts if t["ret"] > 0)
    ex = [t["ret"] - t["bm"] for t in ts if t["bm"] is not None]
    return {"n": len(ts), "win": wins, "rate": round(wins / len(ts), 3), "avg": round(sum(t["ret"] for t in ts) / len(ts), 4),
            "best": max(t["ret"] for t in ts), "worst": min(t["ret"] for t in ts),
            "days": round(sum(t["days"] for t in ts) / len(ts), 1),
            "excess": round(sum(ex) / len(ex), 4) if ex else None}


def verdict(card_live, card_back):
    """依 10 日前瞻的偏多名單給白話結論；樣本不足就明說。"""
    def judge(c, label):
        b = c["B"].get("10", {"n": 0})
        if b["n"] < 30:
            return f"{label}：樣本不足（偏多名單 10 日結果 {b['n']} 筆，至少需 30 筆）"
        if b["excess"] > 0 and b["lb"] > 0.5:
            return f"{label}：目前看起來有效（平均超額 {b['excess'] * 100:+.2f}%，命中率 {b['rate'] * 100:.0f}%）"
        if b["excess"] < 0:
            return f"{label}：目前沒有贏過大盤（平均超額 {b['excess'] * 100:+.2f}%），不建議照單操作"
        return f"{label}：結果不明確（平均超額 {b['excess'] * 100:+.2f}%，命中率 {b['rate'] * 100:.0f}%，統計上還不能下結論）"
    return [judge(card_live, "實盤前瞻"), judge(card_back, "歷史重播")]


def finalize(st, hist, dates):
    fill_forward(st, hist, dates)
    eq = st["eq"]
    mdd, peak = 0.0, 0.0
    for _, e, _b in eq:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1) if peak else mdd
    step = max(1, len(eq) // 200)
    live_eq = [x for x in eq if st["start"] and x[0] > st["start"]]
    opens = []
    for p in st["open"]:
        opens.append({"code": p["code"], "name": p["name"], "entry_date": p["entry_date"], "entry": p["entry"], "close": p["last_close"],
                      "ret": round(p["last_close"] * (1 - FEE - TAX) / (p["entry"] * (1 + FEE)) - 1, 4), "age": p["age"]})
    cl, cb = score_card(st["picks"], True), score_card(st["picks"], False)
    st["stats"] = {
        "live": {"card": cl, "trades": trade_stats(st["trades"], True)},
        "backfill": {"card": cb, "trades": trade_stats(st["trades"], False)},
        "verdict": verdict(cl, cb),
        "by_signal": by_signal([p for p in st["picks"] if p["s"] == "B"]), "by_signal_sell": by_signal([p for p in st["picks"] if p["s"] == "S"]),
        "equity": {"dates": [x[0] for x in eq][::step], "strategy": [x[1] for x in eq][::step], "bench": [x[2] for x in eq][::step],
                   "total": round(eq[-1][1] - 1, 4) if eq else 0, "bench_total": round(eq[-1][2] - 1, 4) if eq else 0, "mdd": round(mdd, 4),
                   "live_total": round(live_eq[-1][1] / [x for x in eq if x[0] == st["start"]][0][1] - 1, 4) if live_eq else None},
        "open_n": len(opens), "unrealized": round(sum(o["ret"] for o in opens) / MAX_POS, 4),
        "live_start": st["start"], "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "sig_names": bt.SIG_NAMES,
    }


def run():
    with open(os.path.join(ROOT, "stocks_hist.json"), encoding="utf-8") as f:
        hist = json.load(f)
    dates = sorted(hist["d"])
    if len(dates) < ss.MIN_BARS:
        raise SystemExit("資料不足，請先執行 fetch_stocks.py")
    st = load_state()
    first_run = st["last"] is None
    todo = [i for i, d in enumerate(dates) if (st["last"] is None or d > st["last"]) and i >= ss.MIN_BARS - 1]
    if first_run and todo:
        for j in range(1, todo[0]):               # 先補齊開始日之前的基準
            st["bm"][dates[j]] = bm_day(hist, dates, j)
    for i in todo:
        process_day(st, hist, dates, i, live=not first_run)
    if first_run and todo:
        st["start"] = st["last"]                  # 此日（含）以前＝回補；之後新增的＝實盤前瞻
    keep = set(dates)
    st["bm"] = {d: v for d, v in st["bm"].items() if d in keep or d >= (st["start"] or "")}
    finalize(st, hist, dates)
    write_json(STATE, st)
    s = st["stats"]
    print(f"[track] 處理 {len(todo)} 日｜最新 {st['last']}｜持股 {len(st['open'])}｜已結 {len(st['trades'])} 筆｜"
          f"淨值 {s['equity']['total'] * 100:+.1f}%（母體 {s['equity']['bench_total'] * 100:+.1f}%）")
    for line in s["verdict"]:
        print("[track]", line)


if __name__ == "__main__":
    run()
