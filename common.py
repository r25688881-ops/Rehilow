"""共用工具：TWSE 抓取（含重試與節流）、路徑、數字解析。僅使用標準函式庫。"""
import json
import os
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = ROOT  # 全部檔案放根目錄（GitHub 網頁無法上傳資料夾）
HIST = os.path.join(ROOT, "hist.json")  # 每日快照合併成單一檔
UA = "Mozilla/5.0 (compatible; tw-radar/1.0; +https://github.com)"


def get_json(url, retries=3, pause=1.6, timeout=25):
    """抓 JSON；失敗回傳 None。TWSE 對高頻請求會封鎖，因此每次請求後固定等待。"""
    last = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read().decode("utf-8", "ignore")
            time.sleep(pause)
            return json.loads(body)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as e:
            last = e
            time.sleep(pause * (i + 2))
    print(f"[warn] 抓取失敗 {url} -> {last}")
    return None


def num(s):
    """'1,234.50' / '--' / '+12' -> float 或 None"""
    if s is None:
        return None
    s = str(s).replace(",", "").replace("+", "").strip()
    if s in ("", "--", "-", "X0.00", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def roc_to_iso(s):
    """'115/11/26' -> '2026-11-26'"""
    y, m, d = s.split("/")
    return f"{int(y) + 1911:04d}-{int(m):02d}-{int(d):02d}"


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
