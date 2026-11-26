"""每日選股：產出『偏多 10 檔』與『偏空 10 檔』→ stocks.json
資料：TWSE 當日個股行情(STOCK_DAY_ALL)＋三大法人買賣超(T86)。
每次執行會把當日快照存到 hist.json，累積越久，動能/均線因子越完整（預設保留 90 天）。
用法：python screen.py [--demo]
"""
import json
import os
import random
import re
import sys
import datetime as dt

from common import DATA, HIST, get_json, num, write_json

KEEP_DAYS = 90
MIN_VALUE = 5e7      # 成交金額 ≥ 5000 萬
MIN_PRICE = 10.0


def fetch_day():
    """回傳 (iso_date, {code: {...}})。往回找最近有資料的交易日。"""
    today = dt.date.today()
    for back in range(0, 8):
        day = today - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        ds = day.strftime("%Y%m%d")
        px = get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/STOCK_DAY_ALL?date={ds}&response=json")
        if not px or px.get("stat") != "OK" or not px.get("data"):
            continue
        # 回傳內容若不是該日（部分情況會回最新日），以其 date 欄為準
        real = str(px.get("date", ds))
        iso = f"{real[:4]}-{real[4:6]}-{real[6:8]}"
        fields = px["fields"]
        ix = {k: fields.index(k) for k in ("證券代號", "證券名稱", "成交股數", "成交金額", "收盤價") if k in fields}
        stocks = {}
        for r in px["data"]:
            code = r[ix["證券代號"]].strip()
            if not re.fullmatch(r"[1-9]\d{3}", code):   # 只留 4 碼一般股，排除 ETF/權證
                continue
            close, vol, val = num(r[ix["收盤價"]]), num(r[ix["成交股數"]]), num(r[ix["成交金額"]])
            if None in (close, vol, val):
                continue
            stocks[code] = {"name": r[ix["證券名稱"]].strip(), "close": close, "vol": vol, "val": val}
        t86 = get_json(f"https://www.twse.com.tw/rwd/zh/fund/T86?date={real}&selectType=ALL&response=json")
        if t86 and t86.get("stat") == "OK":
            f2 = t86["fields"]
            def col(*keys):
                for i, name in enumerate(f2):
                    if all(k in name for k in keys):
                        return i
                return None
            i_f = col("外陸資買賣超股數", "不含外資自營商")
            i_t = col("投信買賣超股數")
            i_a = col("三大法人買賣超股數")
            for r in t86["data"]:
                code = r[0].strip()
                if code in stocks:
                    stocks[code]["foreign"] = num(r[i_f]) if i_f is not None else 0
                    stocks[code]["trust"] = num(r[i_t]) if i_t is not None else 0
                    stocks[code]["inst"] = num(r[i_a]) if i_a is not None else 0
        return iso, stocks
    return None, {}


def _read_hist():
    if os.path.exists(HIST):
        with open(HIST, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_hist(iso, stocks):
    h = _read_hist()
    h[iso] = {c: [s["close"], s["vol"]] for c, s in stocks.items()}
    for old in sorted(h)[:-KEEP_DAYS]:
        del h[old]
    write_json(HIST, h)


def load_hist():
    h = _read_hist()
    return [(d, h[d]) for d in sorted(h)]


def pct_rank(vals):
    """回傳每個值在群體中的百分位(0~1)。"""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    for r, i in enumerate(order):
        out[i] = r / max(1, len(vals) - 1)
    return out


def score(iso, stocks, hist):
    codes = [c for c, s in stocks.items() if s["close"] >= MIN_PRICE and s["val"] >= MIN_VALUE and "inst" in s]
    past = [h for d, h in hist if d < iso]
    feats = {}
    for c in codes:
        s = stocks[c]
        vol = max(s["vol"], 1)
        f = {"inst_r": (s["foreign"] + s["trust"]) / vol, "trust_r": s["trust"] / vol}
        closes = [h[c][0] for h in past if c in h]
        vols = [h[c][1] for h in past if c in h]
        f["ret5"] = s["close"] / closes[-5] - 1 if len(closes) >= 5 else None
        f["ret20"] = s["close"] / closes[-20] - 1 if len(closes) >= 20 else None
        f["ma20"] = (sum(closes[-19:]) + s["close"]) / 20 if len(closes) >= 19 else None
        f["volx"] = s["vol"] / (sum(vols[-5:]) / 5) if len(vols) >= 5 and sum(vols[-5:]) > 0 else None
        feats[c] = f
    # 因子 → 百分位。缺資料的因子給中性 0.5，並在 reason 中註明「歷史累積中」
    def pr(key):
        vals = [feats[c][key] if feats[c][key] is not None else None for c in codes]
        known = [v for v in vals if v is not None]
        if len(known) < 30:
            return [0.5] * len(codes)
        srt = sorted(known)
        return [0.5 if v is None else sum(1 for k in srt if k < v) / max(1, len(srt) - 1) for v in vals]
    P = {k: pr(k) for k in ("inst_r", "trust_r", "ret5", "ret20", "volx")}
    res = []
    for i, c in enumerate(codes):
        s, f = stocks[c], feats[c]
        above = 0.5 if f["ma20"] is None else (1.0 if s["close"] > f["ma20"] else 0.0)
        buy = 100 * (0.35 * P["inst_r"][i] + 0.20 * P["trust_r"][i] + 0.15 * P["ret20"][i] + 0.10 * P["ret5"][i] + 0.10 * above + 0.10 * P["volx"][i])
        sell = 100 * (0.35 * (1 - P["inst_r"][i]) + 0.20 * (1 - P["trust_r"][i]) + 0.15 * (1 - P["ret20"][i]) + 0.10 * (1 - P["ret5"][i]) + 0.10 * (1 - above) + 0.10 * P["volx"][i])
        rs = []
        if s["foreign"] > 0 and s["trust"] > 0:
            rs.append("外資+投信同步買超")
        elif s["trust"] > 0:
            rs.append("投信買超")
        elif s["foreign"] > 0:
            rs.append("外資買超")
        if s["foreign"] < 0 and s["trust"] < 0:
            rs.append("外資+投信同步賣超")
        elif s["foreign"] < 0:
            rs.append("外資賣超")
        elif s["trust"] < 0:
            rs.append("投信賣超")
        if f["ret20"] is not None:
            rs.append(f"20日{f['ret20'] * 100:+.1f}%")
        if f["ma20"] is not None:
            rs.append("站上月線" if above == 1 else "跌破月線")
        if f["volx"] is not None and f["volx"] > 1.5:
            rs.append(f"量增{f['volx']:.1f}倍")
        res.append({"code": c, "name": s["name"], "close": s["close"], "buy": round(buy, 1), "sell": round(sell, 1),
                    "foreign_lots": round(s["foreign"] / 1000), "trust_lots": round(s["trust"] / 1000), "reasons": rs})
    return res, len(past)


def demo_day():
    random.seed(dt.date.today().toordinal())
    stocks = {}
    names = "台積電 鴻海 聯發科 台達電 廣達 緯創 長榮 陽明 中信金 富邦金 國泰金 華碩 技嘉 日月光 聯電 南亞 台塑 研華 瑞昱 世芯".split()
    for i in range(240):
        code = str(1101 + i * 7)
        close = round(random.uniform(15, 900), 1)
        vol = random.uniform(2e6, 8e7)
        stocks[code] = {"name": (names[i % len(names)] + f"{i}"), "close": close, "vol": vol, "val": close * vol,
                        "foreign": random.gauss(0, vol * 0.05), "trust": random.gauss(0, vol * 0.01), "inst": 0}
    return dt.date.today().isoformat(), stocks


def run():
    if "--demo" in sys.argv:
        iso, stocks = demo_day()
    else:
        iso, stocks = fetch_day()
    if not stocks:
        print("[screen] 取不到當日資料（休市或被封鎖），保留舊的 stocks.json")
        return
    hist = load_hist()
    if "--demo" not in sys.argv:
        save_hist(iso, stocks)
    res, hist_days = score(iso, stocks, hist)
    buy = sorted(res, key=lambda x: -x["buy"])[:10]
    sell = sorted(res, key=lambda x: -x["sell"])[:10]
    out = {"date": iso, "generated": dt.datetime.now().isoformat(timespec="seconds"), "universe": len(res),
           "history_days": hist_days, "demo": "--demo" in sys.argv, "buy": buy, "sell": sell}
    write_json(os.path.join(DATA, "stocks.json"), out)
    print(f"[screen] {iso} 母體 {len(res)} 檔，歷史累積 {hist_days} 日")


if __name__ == "__main__":
    run()
