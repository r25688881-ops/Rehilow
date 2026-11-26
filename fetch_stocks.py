"""下載上市個股每日行情（TWSE MI_INDEX，每個交易日 1 次請求），增量更新 stocks_hist.json。
只保留一般股票（4 碼、非 0 開頭，排除 ETF），並只保留最近約 200 個日曆日。
用法：python fetch_stocks.py
"""
import datetime as dt
import json
import os
import sys

from common import ROOT, get_json, num

HISTF = os.path.join(ROOT, "stocks_hist.json")
BACK_DAYS = 200      # 首次回補的日曆天數（約 135 個交易日）
KEEP_DAYS = 220      # 檔案保留天數


def load():
    if os.path.exists(HISTF):
        with open(HISTF, encoding="utf-8") as f:
            return json.load(f)
    return {"names": {}, "d": {}}


def parse(j):
    """從 MI_INDEX 回應找出『每日收盤行情』表，回傳 {code: (name,[o,h,l,c,vol,val])}。"""
    cands = []
    for t in j.get("tables") or []:
        cands.append((t.get("fields") or [], t.get("data") or []))
    if j.get("fields9") and j.get("data9"):
        cands.append((j["fields9"], j["data9"]))
    for fields, data in cands:
        if "證券代號" in fields and "收盤價" in fields:
            ix = {k: fields.index(k) for k in ("證券代號", "證券名稱", "成交股數", "成交金額", "開盤價", "最高價", "最低價", "收盤價")}
            out = {}
            for r in data:
                code = str(r[ix["證券代號"]]).strip()
                if len(code) != 4 or not code.isdigit() or code.startswith("0"):
                    continue
                o, h, l, c, v, val = (num(r[ix[k]]) for k in ("開盤價", "最高價", "最低價", "收盤價", "成交股數", "成交金額"))
                if None in (o, h, l, c, v, val) or c <= 0:
                    continue
                out[code] = (str(r[ix["證券名稱"]]).strip(), [o, h, l, c, v, val])
            return out
    return {}


def run():
    hist = load()
    today = dt.date.today()
    if hist["d"]:
        start = dt.date.fromisoformat(max(hist["d"])) + dt.timedelta(days=1)
    else:
        start = today - dt.timedelta(days=BACK_DAYS)
    added, d = 0, start
    while d <= today:
        if d.weekday() < 5:
            j = get_json(f"https://www.twse.com.tw/rwd/zh/afterTrading/MI_INDEX?date={d:%Y%m%d}&type=ALL&response=json")
            if j and j.get("stat") == "OK":
                rows = parse(j)
                if rows:
                    hist["d"][d.isoformat()] = {k: v[1] for k, v in rows.items()}
                    for k, v in rows.items():
                        hist["names"][k] = v[0]
                    added += 1
        d += dt.timedelta(days=1)
    cutoff = (today - dt.timedelta(days=KEEP_DAYS)).isoformat()
    hist["d"] = {k: v for k, v in hist["d"].items() if k >= cutoff}
    with open(HISTF, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, separators=(",", ":"))
    n = len(hist["d"])
    print(f"[stocks] 共 {n} 個交易日，新增 {added}，最新 {max(hist['d']) if n else '-'}")
    if n < 70:
        print("[error] 交易日不足 70 天，無法計算指標（可能被 TWSE 封鎖）")
        sys.exit(1)


if __name__ == "__main__":
    run()
