# 貢獻與回報問題

目前最有價值的協助是乾淨環境的安裝回報、文件錯誤，以及可重現的代辦操作問題。大型功能、跨校 Study 支援或多使用者設計，請先開 Issue 描述需求與範圍。

## 開始之前

Repository 尚未提供 License。請先釐清授權再提出程式碼貢獻；此指南不授予任何額外使用或再散布權利。目前可先透過 Issue 回報問題或提出文件建議。

## 安裝與檢查

依 [本機啟動指南](docs/getting-started.md) 安裝。一般測試不需真實 Gemini key、Telegram token、學校帳密或 GCP project；外部互動多由測試替身驗證，不代表 live integration。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_docs.py
.\.venv\Scripts\python.exe scripts/check_demo.py
```

有修改 Chrome extension 時，另外使用 Node.js 的內建測試執行器：

```powershell
node --test chrome-extension/*.test.cjs chrome-extension/*.test.js chrome-extension/test/*.test.js
```

也可用 `.venv/bin/python` 在 macOS / Linux 執行上述 Python 指令。CI 設定會在 Python 3.11 / 3.12 執行測試、文件相對檔案連結檢查及 Demo 隔離驗證，並另以 Node.js 22 執行 Chrome extension 測試。新 workflow 尚需推送後才會在 GitHub 執行；本機通過不代表遠端已執行。文件檢查不連網，也不驗證 heading anchors；外部連結仍需另行查核。

## 好的 Issue

- 安裝問題：作業系統、Python 版本、使用哪份文件、停在哪一步。
- Bug：最小重現步驟、預期行為、實際行為、commit 或版本。
- 功能提議：具體使用情境、現有方式的限制、可接受的最小改動。

請使用合成任務內容，移除 bot token、API key、chat ID、cookies、個人代辦、教材及學校登入資料。不要上傳 `.env` 或完整資料庫。無須公開 production 資源存取資訊。

## 提交變更

在授權釐清後，讓 PR 專注於一個問題，說明前後行為與實際完成的檢查。文件不能將未部署功能、mock 測試、部分觀察或計畫描述成已上線成果。只有實際可重現且範圍清楚的問題才適合標記為新手可貢獻；不要為了增加活動量建立無實質內容的 Issue。
