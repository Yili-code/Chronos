# 本機 Web 示範

這個示範使用實際 Chronos Web 頁面與 SQLite 路徑，預填兩筆合成任務，讓你先看清單、期限、分類與完成操作。不是 Gemini 或 Telegram 的 end-to-end 示範，也不是實際使用者資料。

![Actual Chronos Web dashboard showing synthetic report and setup-guide tasks](images/web-demo.png)

截圖於 2026-10-07 以本機示範程式擷取；報告期限由執行當下的隔天 17:00 產生，你的畫面日期會不同。已以瀏覽器驗證清單載入、兩筆任務完成與未配置 Gemini 時新增失敗且不寫入資料。

先依 [啟動指南](getting-started.md) 安裝套件，再在根目錄執行：

```powershell
.\.venv\Scripts\python.exe scripts/demo_local.py
```

macOS / Linux：

```bash
.venv/bin/python scripts/demo_local.py
```

開啟 <http://127.0.0.1:8000>，會看到 **Finish the report** 和 **Read the setup guide**。按 **Complete** 可以完成合成任務。**Add 在此示範停用 Gemini，按下會收到設定錯誤**；要驗證自然語言新增，請依一般啟動流程提供自己的 key。

示範覆寫資料庫、外部 AI、Telegram 與排程設定，不使用既有 `.env` 的服務連線。資料放在暫存 SQLite，正常 Ctrl+C 結束時刪除；異常中斷可能留下暫存檔，不含個人任務。已有服務占用 8000 時用 `--port 8001`，改開對應網址。

可分享的核心情境是「收下明天要交的報告 → 查期限 → 完成」，需要 Gemini / Telegram 的真實示範仍須使用你自己的測試 bot，並遮蔽身分與憑證。Study 教材示範需要資料使用權，不能以私有課程內容作為公共素材。
