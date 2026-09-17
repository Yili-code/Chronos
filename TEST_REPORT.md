# Chronos 功能測試報告

日期：2026-09-17（Asia/Taipei）

結果：26 passed、5 xfailed、3 dependency deprecation warnings。5 個 xfail 是實際重現、尚未修復的缺陷，不能視為通過。使用 strict=True，修復後須移除對應標記。

## 已驗證

| 功能 | 結果與驗證範圍 |
| --- | --- |
| Web/API | HTTP 層測試首頁、健康狀態、登入成功/失敗、未登入禁止讀寫、輸入驗證、新增、查詢、完成及不存在代辦 |
| 網頁操作 | 隔離資料庫啟動實際服務，瀏覽器確認專案列表、代辦完成、特殊字元以純文字顯示、AI 未設定提示、失敗後保留輸入並恢復按鈕 |
| SQLite | 新增、重新連線後保存、期限排序、完成、延期、已完成項目不可延期 |
| AI | 模擬供應商成功、JSON 格式錯誤、缺少時區、空標題、401/429/500、逾時；失敗不寫入代辦 |
| Telegram | 本機 HTTP webhook 測試 secret/chat 驗證、說明、新增、查詢、完成、延期、專案；傳送使用 mock |
| 每日提醒 | 驗證 Asia/Taipei 08:00 排程，直接呼叫提醒函式檢查收件者與內容；未等待真實排程送達 |
| Git | 建立隔離 repository，驗證 branch、commit、乾淨及修改狀態；實際開發目錄偵測到 4 個 repository |
| GitHub | 模擬 metadata 成功及 HTTP 403/404/429/500；實際帳號 API 回傳 200 |
| Telegram 連線 | 實際 getMe/getWebhookInfo 回傳 200；未註冊 webhook、pending updates 為 0 |

## 可重現缺陷

1. **Git 失敗時誤報乾淨**：`chronos/projects.py` 的 `_git` 忽略 stderr，失敗回傳空字串。實際遇到 sandbox 帳號被 Git ownership 檢查拒絕，UI 顯示「乾淨／尚無 commit」。這代表未知狀態被當成正常狀態。
2. **Telegram 重送造成重複代辦**：同一 `update_id` 連續送兩次，新增兩筆。webhook 未實作去重。
3. **GitHub 網路錯誤中斷專案掃描**：模擬 ConnectError，`_github_status` 未捕捉；可一路傳遞至專案 API。HTTP 錯誤已有處理，但連線例外沒有。
4. **含句點的 GitHub repo 名稱無法辨識**：`https://github.com/owner/my.repo.git` 解析為 None，略過 GitHub metadata。
5. **格式錯誤的 webhook 回傳 500**：合法 JSON 陣列 `[]` 無法通過 `.get()`，應提供受控的輸入錯誤回應。

## 尚未完成的實際環境驗證

- 外部 AI 的網址、API key、模型皆未設定，不能驗證真實模型解析效果。
- Telegram 公開網址與 webhook secret 未設定，Telegram 端也沒有 webhook；尚未驗證真正收件、回覆或每日提醒送達。此次未發送任何實際訊息。
- Docker CLI 存在，但未連上 Docker engine；本次未驗證映像建置及容器運作。
- 實際 GitHub 帳號認證成功不代表所有目標 repository metadata 權限均可用；metadata 功能使用模擬回應測試。
- Web 密碼目前未設定；Basic Auth 功能以隔離設定驗證。

## 重跑

```powershell
.\.venv\Scripts\python.exe -m pytest -q -rx --basetemp=.pytest-tmp/verify
```

新增測試位於 `tests/test_system.py`。既有 15 項測試，加上 11 項一般測試與 5 項缺陷重現測試，共 31 項。測試資料位於 `.pytest-tmp`，未修改使用者 `.env` 或正式代辦資料。瀏覽器測試服務已停止。應用程式實作未修改。
