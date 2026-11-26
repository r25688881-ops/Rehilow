# AGENTS.md — 給 Codex 的專案指引

## 專案
台股反轉雷達：加權指數 20 年回測 → 每日買/賣建議 → 買賣各 10 檔觀察名單。純靜態 PWA（可離線），由 GitHub Actions 每日更新資料，GitHub Pages 發佈。

## 規則
- UI 一律使用**繁體中文**；風格：深色、科技感、簡潔；保留 icon（icon 檔）與離線功能（`sw.js`、`manifest.webmanifest`）。
- Python 只用標準函式庫（不要新增依賴）。程式在 ``，輸出在 `*.json`。
- 改動訊號或回測時，**必須維持樣本外驗證**（`backtest.py` 的訓練/測試切分），不可用全樣本結果宣稱準確率。
- 修改 `sw.js` 或 App 外殼檔案後，請遞增 `sw.js` 內的 `VER`。
- 不得在畫面或文件中宣稱「保證獲利」或固定準確率。

## 常用指令
```bash
python fetch_taiex.py        # 下載/增量更新加權指數
python backtest.py           # 回測 → signals.json
python screen.py             # 選股 → stocks.json
python -m http.server 8000           # 本機預覽 http://localhost:8000
# 無網路時測試：python fetch_taiex.py --demo && python backtest.py && python screen.py --demo
```

## 待辦（優先順序）
1. 加入融資維持率、VIXTWN、外資台指期未平倉的逐日歷史（Actions 累積），納入訊號。
2. 加入週線 KD/RSI 確認層。
3. 滾動式前進驗證（多段切分）＋打亂資料對照組。
4. 個股層級回測（目前個股名單為多因子排序，尚未回測）。
