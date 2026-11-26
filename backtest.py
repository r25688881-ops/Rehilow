"""加權指數 20 年反轉訊號回測 + 前進式(walk-forward)驗證 + 今日建議。
輸入：taiex.csv    輸出：signals.json
純標準函式庫。重點是『誠實的驗證』：先在前 60% 期間挑組合，再用後 40% 沒看過的資料檢驗。
"""
import csv
import itertools
import math
import os
import datetime as dt

from common import DATA, write_json

HORIZON = 20       # 前瞻報酬天數（約一個月）
WINDOW = 5         # 組合訊號：成員需在近 5 日內都出現過
DEDUP = 10         # 事件去重：同訊號 10 日內只算一次，避免重複計算同一波
MIN_TRAIN, MIN_TEST = 8, 6
COST = 0.002       # 每次切換部位成本（ETF 約略估計）
TRAIN_FRAC = 0.6


# ---------- 指標 ----------
def sma(x, n):
    out, s = [None] * len(x), 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(x, n):
    k, out, e = 2 / (n + 1), [], None
    for v in x:
        e = v if e is None else v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(c, n=14):
    out = [None] * len(c)
    if len(c) <= n:
        return out
    g = [max(c[i] - c[i - 1], 0) for i in range(1, len(c))]
    l = [max(c[i - 1] - c[i], 0) for i in range(1, len(c))]
    ag, al = sum(g[:n]) / n, sum(l[:n]) / n
    for i in range(n, len(c)):
        if i > n:
            ag = (ag * (n - 1) + g[i - 1]) / n
            al = (al * (n - 1) + l[i - 1]) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def kd(h, l, c, n=9):
    K, D, k, d = [None] * len(c), [None] * len(c), 50.0, 50.0
    for i in range(len(c)):
        if i < n - 1:
            continue
        hh, ll = max(h[i - n + 1:i + 1]), min(l[i - n + 1:i + 1])
        rsv = 50.0 if hh == ll else (c[i] - ll) / (hh - ll) * 100
        k = k * 2 / 3 + rsv / 3
        d = d * 2 / 3 + k / 3
        K[i], D[i] = k, d
    return K, D


def macd_hist(c):
    m = [a - b for a, b in zip(ema(c, 12), ema(c, 26))]
    s = ema(m, 9)
    return [a - b for a, b in zip(m, s)]


def boll(c, n=20, k=2.0):
    mid = sma(c, n)
    up, lo = [None] * len(c), [None] * len(c)
    for i in range(n - 1, len(c)):
        w = c[i - n + 1:i + 1]
        sd = math.sqrt(sum((v - mid[i]) ** 2 for v in w) / n)
        up[i], lo[i] = mid[i] + k * sd, mid[i] - k * sd
    return mid, up, lo


def load():
    d, o, h, l, c = [], [], [], [], []
    with open(os.path.join(DATA, "taiex.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            d.append(r["date"]); o.append(float(r["open"])); h.append(float(r["high"]))
            l.append(float(r["low"])); c.append(float(r["close"]))
    return d, o, h, l, c


# ---------- 訊號（True=當日觸發） ----------
SIG_NAMES = {
    "B_RSI": "RSI<30 且回升（超賣反彈）",
    "B_KD": "KD 低檔(K<25)黃金交叉",
    "B_BIAS": "季線乖離 < -8%（負乖離過大）",
    "B_BOLL": "跌破布林下軌後收回",
    "B_MACD": "MACD 柱狀體負值轉強",
    "B_HAMMER": "長下影線(落底K棒)且短線超跌",
    "B_PANIC": "5 日急殺 -6% 以上（恐慌殺盤）",
    "S_RSI": "RSI>70 且回落（超買轉弱）",
    "S_KD": "KD 高檔(K>75)死亡交叉",
    "S_BIAS": "季線乖離 > +8%（正乖離過大）",
    "S_BOLL": "突破布林上軌後跌回",
    "S_MACD": "MACD 柱狀體正值轉弱",
    "S_STAR": "長上影線(見頂K棒)且短線過熱",
    "S_SURGE": "5 日急漲 +7% 以上（過熱）",
}


def build_signals(o, h, l, c):
    n = len(c)
    R, K, D = rsi(c), *kd(h, l, c)
    M = macd_hist(c)
    ma60 = sma(c, 60)
    ma20 = sma(c, 20)
    _, bu, bl = boll(c)
    S = {k: [False] * n for k in SIG_NAMES}
    for i in range(61, n):
        bias = c[i] / ma60[i] - 1
        bias20 = c[i] / ma20[i] - 1
        rng = max(h[i] - l[i], 1e-9)
        lower = (min(o[i], c[i]) - l[i]) / rng
        upper = (h[i] - max(o[i], c[i])) / rng
        r5 = c[i] / c[i - 5] - 1
        S["B_RSI"][i] = R[i] < 30 and R[i] > R[i - 1]
        S["S_RSI"][i] = R[i] > 70 and R[i] < R[i - 1]
        S["B_KD"][i] = K[i] < 25 and K[i - 1] <= D[i - 1] and K[i] > D[i]
        S["S_KD"][i] = K[i] > 75 and K[i - 1] >= D[i - 1] and K[i] < D[i]
        S["B_BIAS"][i] = bias < -0.08
        S["S_BIAS"][i] = bias > 0.08
        S["B_BOLL"][i] = c[i - 1] < bl[i - 1] and c[i] > bl[i]
        S["S_BOLL"][i] = c[i - 1] > bu[i - 1] and c[i] < bu[i]
        S["B_MACD"][i] = M[i] < 0 and M[i] > M[i - 1] and M[i - 1] <= M[i - 2]
        S["S_MACD"][i] = M[i] > 0 and M[i] < M[i - 1] and M[i - 1] >= M[i - 2]
        S["B_HAMMER"][i] = lower > 0.6 and rng / c[i] > 0.015 and bias20 < -0.03
        S["S_STAR"][i] = upper > 0.6 and rng / c[i] > 0.015 and bias20 > 0.03
        S["B_PANIC"][i] = r5 < -0.06
        S["S_SURGE"][i] = r5 > 0.07
    snap = {"RSI14": R[-1], "K": K[-1], "D": D[-1], "MACD柱": M[-1], "季線乖離%": (c[-1] / ma60[-1] - 1) * 100 if ma60[-1] else None,
            "月線乖離%": (c[-1] / ma20[-1] - 1) * 100 if ma20[-1] else None}
    return S, snap


def active(sig, w=WINDOW):
    out, last = [False] * len(sig), -10 ** 9
    for i, v in enumerate(sig):
        if v:
            last = i
        out[i] = i - last < w
    return out


def combo_events(names, S, n):
    """組合『剛成立』的日期（各成員近 WINDOW 日內都出現過，且昨天尚未成立），並去重。"""
    acts = [active(S[k]) for k in names]
    ev, last, prev = [], -10 ** 9, False
    for i in range(n):
        now = all(a[i] for a in acts)
        if now and not prev and i - last >= DEDUP:
            ev.append(i)
            last = i
        prev = now
    return ev


def wilson_lb(k, n, z=1.64):
    if n == 0:
        return 0.0
    p = k / n
    den = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / den


def evaluate(ev, c, side, lo, hi):
    """side=+1 買進(看漲)；-1 賣出(看跌)。僅計 [lo,hi) 區間內且有完整前瞻資料的事件。"""
    rets = [c[i + HORIZON] / c[i] - 1 for i in ev if lo <= i < hi and i + HORIZON < len(c)]
    n = len(rets)
    hit = sum(1 for r in rets if r * side > 0)
    avg = sum(rets) / n * side if n else 0.0
    return {"n": n, "hit": hit, "rate": hit / n if n else 0.0, "avg": avg, "lb": wilson_lb(hit, n)}


def run():
    d, o, h, l, c = load()
    n = len(c)
    if n < 700:
        raise SystemExit("資料不足，請先執行 fetch_taiex.py")
    S, snap = build_signals(o, h, l, c)
    split = int(n * TRAIN_FRAC)
    warm = 61
    # 基準：無條件持有 20 日上漲機率（用來判斷訊號是否真的比亂猜好）
    base_up = lambda lo, hi: sum(1 for i in range(lo, hi - HORIZON) if c[i + HORIZON] > c[i]) / max(1, hi - HORIZON - lo)
    base = {"train_up": base_up(warm, split), "test_up": base_up(split, n), "all_up": base_up(warm, n)}

    result = {}
    for side, pre in ((+1, "B_"), (-1, "S_")):
        names = [k for k in SIG_NAMES if k.startswith(pre)]
        singles = []
        for k in names:
            ev = combo_events([k], S, n)
            singles.append({"id": k, "name": SIG_NAMES[k], "all": evaluate(ev, c, side, warm, n),
                            "train": evaluate(ev, c, side, warm, split), "test": evaluate(ev, c, side, split, n)})
        combos = []
        for r in (2, 3):
            for cb in itertools.combinations(names, r):
                ev = combo_events(cb, S, n)
                tr, te = evaluate(ev, c, side, warm, split), evaluate(ev, c, side, split, n)
                al = evaluate(ev, c, side, warm, n)
                # 基準：買進看「上漲機率」，賣出看「下跌機率」
                b_tr = base["train_up"] if side > 0 else 1 - base["train_up"]
                b_te = base["test_up"] if side > 0 else 1 - base["test_up"]
                robust = tr["n"] >= MIN_TRAIN and te["n"] >= MIN_TEST and tr["rate"] > b_tr + 0.05 and tr["lb"] > b_tr and te["rate"] > b_te + 0.05 and te["avg"] > 0
                combos.append({"ids": list(cb), "names": [SIG_NAMES[x] for x in cb], "all": al, "train": tr, "test": te, "robust": robust})
        combos.sort(key=lambda x: (x["robust"], x["train"]["lb"]), reverse=True)
        result["buy" if side > 0 else "sell"] = {"singles": singles, "combos": combos[:12]}

    rules = {k: [x for x in result[k]["combos"] if x["robust"]][:3] for k in ("buy", "sell")}

    # 今日啟動的規則
    def is_on(ids):
        return all(active(S[i])[-1] for i in ids)

    on_buy = [r for r in rules["buy"] if is_on(r["ids"])]
    on_sell = [r for r in rules["sell"] if is_on(r["ids"])]
    single_on = {k: bool(S[k][-1]) for k in SIG_NAMES if S[k][-1]}
    if on_buy and not on_sell:
        act, label = "buy", "偏多：出現反轉向上組合"
    elif on_sell and not on_buy:
        act, label = "sell", "偏空：出現反轉向下組合"
    elif on_buy and on_sell:
        act, label = "hold", "訊號衝突：觀望"
    else:
        # 沒有通過驗證的組合啟動 → 以單一訊號數量給較弱的提示
        nb = sum(1 for k in single_on if k.startswith("B_"))
        ns = sum(1 for k in single_on if k.startswith("S_"))
        act, label = ("hold", "無通過驗證的反轉組合：持有/觀望")
        if nb > ns:
            label += f"（單一偏多訊號 {nb} 個，僅供參考）"
        elif ns > nb:
            label += f"（單一偏空訊號 {ns} 個，僅供參考）"

    # ---------- 策略淨值（預設持有；賣出規則→空手 HORIZON 日；買進規則→持有）----------
    buy_ev = sorted({i for r in rules["buy"] for i in combo_events(r["ids"], S, n)})
    sell_ev = sorted({i for r in rules["sell"] for i in combo_events(r["ids"], S, n)})
    pos, flat_until, eq, bh, trades = 1, -1, 1.0, 1.0, 0
    eqs, bhs, dates = [], [], []
    bset, sset = set(buy_ev), set(sell_ev)
    for i in range(1, n):
        if i in sset and pos == 1:
            pos, flat_until, eq, trades = 0, i + HORIZON, eq * (1 - COST), trades + 1
        if (i in bset or i >= flat_until > 0) and pos == 0:
            pos, eq, trades = 1, eq * (1 - COST), trades + 1
        r = c[i] / c[i - 1] - 1
        eq *= 1 + (r if pos else 0)
        bh *= 1 + r
        if i >= split - 1:  # 只記錄樣本外（測試期）
            eqs.append(eq), bhs.append(bh), dates.append(d[i])
    def stat(series):
        yrs = max(len(series) / 245, 1e-9)
        peak, mdd = series[0], 0.0
        for v in series:
            peak = max(peak, v)
            mdd = min(mdd, v / peak - 1)
        return {"cagr": (series[-1] / series[0]) ** (1 / yrs) - 1, "mdd": mdd}
    if eqs:
        e0, b0 = eqs[0], bhs[0]
        eqs, bhs = [v / e0 for v in eqs], [v / b0 for v in bhs]
    step = max(1, len(eqs) // 220)
    equity = {"dates": dates[::step], "strategy": [round(v, 4) for v in eqs[::step]], "buyhold": [round(v, 4) for v in bhs[::step]],
              "strategy_stat": stat(eqs) if eqs else None, "buyhold_stat": stat(bhs) if bhs else None, "trades": trades}

    demo = os.path.exists(os.path.join(DATA, "DEMO"))
    out = {
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "demo": demo,
        "data_start": d[0], "data_end": d[-1], "n_days": n, "split_date": d[split],
        "close": c[-1], "change_pct": (c[-1] / c[-2] - 1) * 100,
        "horizon": HORIZON, "window": WINDOW,
        "base_rate": base,
        "recommendation": {"action": act, "label": label,
                           "buy_rules_on": [r["names"] for r in on_buy], "sell_rules_on": [r["names"] for r in on_sell],
                           "single_signals_on": [{"id": k, "name": SIG_NAMES[k]} for k in single_on]},
        "indicators": snap,
        "rules": rules,
        "buy": result["buy"], "sell": result["sell"],
        "equity": equity,
        "sig_names": SIG_NAMES,
    }
    write_json(os.path.join(DATA, "signals.json"), out)
    print(f"[backtest] {d[0]}~{d[-1]} 共 {n} 日｜建議：{label}｜穩健買進規則 {len(rules['buy'])}｜穩健賣出規則 {len(rules['sell'])}")


if __name__ == "__main__":
    run()
