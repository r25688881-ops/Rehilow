"""下載加權指數(TAIEX)日 K，增量更新 taiex.csv。
來源：臺灣證券交易所 MI_5MINS_HIST（每月一次請求，欄位：日期/開/高/低/收）。
用法：
  python fetch_taiex.py            # 增量更新，首次會抓約 21 年
  python fetch_taiex.py --demo     # 產生【模擬資料】僅供離線測試介面，不可用於投資判斷
"""
import csv
import datetime as dt
import os
import random
import sys

from common import DATA, get_json, num, roc_to_iso

CSV = os.path.join(DATA, "taiex.csv")
START_YEAR = 2005  # 抓 21 年：多出 1 年當指標暖機


def load():
    rows = {}
    if os.path.exists(CSV):
        with open(CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = [float(r[k]) for k in ("open", "high", "low", "close")]
    return rows


def save(rows):
    os.makedirs(DATA, exist_ok=True)
    with open(CSV, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close"])
        for d in sorted(rows):
            w.writerow([d] + rows[d])


def month_iter(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def run():
    if os.path.exists(os.path.join(DATA, "DEMO")) and os.path.exists(CSV):
        os.remove(CSV)  # 清掉模擬資料，避免混入真實資料
    rows = load()
    today = dt.date.today()
    if rows:
        last = max(rows)
        sy, sm = int(last[:4]), int(last[5:7])  # 從最後一筆所在月份重抓
    else:
        sy, sm = START_YEAR, 1
    added = 0
    for y, m in month_iter((sy, sm), (today.year, today.month)):
        j = get_json(f"https://www.twse.com.tw/rwd/zh/TAIEX/MI_5MINS_HIST?date={y}{m:02d}01&response=json")
        if not j or j.get("stat") != "OK":
            continue
        for r in j.get("data", []):
            try:
                o, h, l, c = (num(x) for x in r[1:5])
                if None in (o, h, l, c):
                    continue
                d = roc_to_iso(r[0])
            except Exception:
                continue
            if d not in rows:
                added += 1
            rows[d] = [o, h, l, c]
    save(rows)
    print(f"[taiex] 共 {len(rows)} 筆，新增 {added}，最新 {max(rows) if rows else '-'}")
    if len(rows) < 1000:
        print("[warn] 資料量偏少，請檢查網路或 TWSE 是否封鎖此 IP")


def demo():
    """合成資料（含多空循環與崩盤），僅供離線 UI/程式測試。"""
    random.seed(7)
    rows, p, d = {}, 6000.0, dt.date(2005, 1, 3)
    drift, vol = 0.0004, 0.011
    while d <= dt.date.today():
        if d.weekday() < 5:
            if random.random() < 0.01:
                drift = random.choice([-0.0015, -0.0005, 0.0005, 0.0012])
                vol = random.choice([0.008, 0.011, 0.018, 0.026])
            r = random.gauss(drift, vol)
            o = p * (1 + random.gauss(0, 0.003))
            c = p * (1 + r)
            h = max(o, c) * (1 + abs(random.gauss(0, vol / 2)))
            l = min(o, c) * (1 - abs(random.gauss(0, vol / 2)))
            rows[d.isoformat()] = [round(x, 2) for x in (o, h, l, c)]
            p = c
        d += dt.timedelta(days=1)
    save(rows)
    print(f"[demo] 已產生模擬資料 {len(rows)} 筆（非真實行情）")
    open(os.path.join(DATA, "DEMO"), "w").write("demo")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
    else:
        run()
        flag = os.path.join(DATA, "DEMO")
        if os.path.exists(flag):
            os.remove(flag)
