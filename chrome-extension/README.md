# Chronos Study Read-only Bridge

這個 development extension 只支援 NTOU TronClass 的 read-only observation、
作業說明與公告。它不是通用 LMS connector。

## Data boundary

- Content script 只讀目前頁面已顯示的文字與識別碼。
- Popup 可明確把 redacted observation、作業說明或公告送到
  `http://127.0.0.1:8765`。
- 作業附件只保留 filename 與 source ID；不下載附件，也不交給 AI。
- Extension 沒有 `downloads` 或 `cookies` 權限。
- 不匯出 browser session、cookie、CSRF token、authenticated URL 或 storage。
- 背景排程只處理公告；它不讀教材頁、不下載 PDF，也不啟動 AI。

## Local setup

1. 在 `chrome://extensions` 開啟 Developer mode。
2. Load unpacked，選擇本目錄並記下 32 字元 extension ID。
3. 從 repository root 啟動 receiver：

   ```powershell
   .\.venv\Scripts\python.exe -m chronos.local_observation_server --extension-id YOUR_32_CHARACTER_EXTENSION_ID
   ```

4. 登入自己的 TronClass，在作業或公告頁打開 extension popup。

Receiver 綁定 loopback，並驗證 exact extension origin。關閉 receiver 後，popup
handoff 會失敗；這不代表 TronClass 沒有資料。

## Tests

```powershell
node --test chrome-extension/*.test.cjs chrome-extension/test/*.test.js
```

這些測試只涵蓋 DOM helper、origin check 與 mocked transport，不證明 live 登入、
完整課程資料或 Telegram delivery。
