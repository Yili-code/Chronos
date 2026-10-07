# Gmail → Telegram → Chronos tasks

此功能需要 Chronos 自己的 Google OAuth 授權；ChatGPT/Codex Gmail 外掛的登入與權限不會自動傳給 Chronos。預設關閉，尚未授權或部署時不會處理信件。

## 已實作的行為

- 每天 **Asia/Taipei（UTC+8）08:00**，檢查未讀信（包含收件匣外的未讀信，不含垃圾桶／垃圾郵件）。每封成功播報一次，仍維持未讀；之後可從原卡片操作，不每天重複洗版。每次預設最多 100 封，剩餘信件於後續執行處理；Gmail 分頁會略過已播報項目。
- 僅同時符合 Gmail `CATEGORY_PROMOTIONS`、`List-Unsubscribe` 與明確促銷詞的信移到垃圾桶。帳單、交易、登入、安全、作業／工作等關鍵字、往返信、星號、重要郵件、私人分類、白名單，以及內容過長而無法完整檢查的信保留。這是保守啟發式，不是完美分類；可先檢視預覽、加入白名單。
- Telegram 逐封簡短回報移到垃圾桶的主旨、寄件者及原因；其他信提供繁體中文摘要、建議下一步與 Gmail 連結。摘要使用既有 Gemini，失敗時顯示原文摘錄。不會開啟郵件內連結或讀取附件。
- 直接按郵件卡片按鈕或回覆 `刪除`、`保留`、`已讀`、`新增任務：要做的事`。使用現有 Telegram webhook 立即處理，不等隔天。
- 新增任務必須由使用者要求。每封信最多建立一個 task，保留原信連結；重複點擊、Telegram 重送與併發更新不會重複建立。後續修改沿用 `/edit`。
- **沒有寄信、轉寄或永久刪除入口**。即使回覆「寄出」也不寄信。未來若新增寄信功能，必須先呈現收件人、主旨、正文並取得使用者對該封內容的明確同意，不能把 Gmail 讀寫授權視為寄信許可。
- 目前只支援既有的私人 Telegram chat（正數 chat ID），且必須設定 webhook secret。群組使用尚未支援。

## 第一次授權

1. 在 [Google Cloud Console](https://console.cloud.google.com/) 選擇自己的 project，啟用 **Gmail API**。
2. 在 **Google Auth Platform** 設定應用名稱與聯絡 email；Audience 使用 External，Testing 階段把自己的 Gmail 加入 test users。
3. Clients → Create client → **Desktop app**。下載 client JSON，放到本專案 `.gmail/client.json`。`.gmail/` 已排除 Git 與 Cloud Build，請勿貼 JSON 或 token 到聊天。
4. 在專案根目錄執行：

```powershell
New-Item -ItemType Directory -Force .gmail | Out-Null
.\.venv\Scripts\python.exe -m chronos.gmail_authorize --client-secret .gmail/client.json --account YOUR_EMAIL@gmail.com
```

瀏覽器會開啟 Google 同意頁。確認正確帳號並授權後，工具會用 PKCE 與隨機 state 驗證本機回呼，再確認 Gmail 帳號符合 `--account`。client ID、secret、refresh token 與帳號存入 `.env`；不會輸出憑證、不會改信、不會開啟郵件功能。Google 同意或重新登入必須由帳號持有人完成。

Gmail 的垃圾桶 API 需要 `gmail.modify`。該 Google scope 也涵蓋寄信能力，**Google 沒有在此 scope 內拆出「寄信前詢問」**；本程式藉由不提供寄信入口限制行為。不要將此 OAuth token 用於未受控的其他程式。參考 [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes)、[trash](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/trash)、[Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)。

External + Testing 的 refresh token 可能有短期有效期限；長期運作前依 Google 對應用發布狀態與帳號的規定完成設定。授權失效時會停止處理，須重新授權，不會退回其他帳號。

## 預覽與本機啟用

先用唯讀預覽查看會被過濾的信（輸出包含主旨和寄件者，勿公開分享）：

```powershell
.\.venv\Scripts\python.exe -m chronos.run_mail
```

在 `.env` 補好既有 Telegram/Gemini 設定，需要時加入白名單：

```dotenv
CHRONOS_GMAIL_KEEP_SENDERS=person@example.com,@company.example
CHRONOS_GMAIL_MAX_MESSAGES=100
CHRONOS_ENABLE_GMAIL=true
```

重啟 Chronos。啟用內建 scheduler 時，服務必須在 08:00 持續運行；排程以 Asia/Taipei 固定。若 08:00 未運行，當日不會自行補跑。

## Cloud Run

完成本機 OAuth 後，使用原部署腳本加上明確旗標：

```powershell
pwsh -File .\scripts\deploy_cloud_run.ps1 -ProjectId YOUR_PROJECT_ID -EnableGmail
```

Gmail 憑證由 Secret Manager 注入，不放進 image。使用既有每天 08:00、Asia/Taipei 的 `/internal/daily` Cloud Scheduler job；該 endpoint 會先發 tasks 再整理信。內建 scheduler 在 Cloud Run 保持關閉。每次需要啟用 Gmail 的部署都要帶 `-EnableGmail`；省略會關閉功能。部署是獨立步驟，新增程式碼或通過本機測試不代表 production 已更新。

## 資料與失敗處理

SQLite 的 `mail_state` 或 Firestore 的 `<prefix>_mail_state` 保存郵件 ID、主旨、寄件者、日期、最多 12,000 字元內文、摘要、原卡片對應與任務／投遞紀錄。這些資料目前不自動到期；資料庫備份亦包含郵件資料。Gemini 會收到摘要或任務解析所需的郵件文字；Telegram 收到主旨、寄件者與摘要／摘錄。

垃圾桶操作前先保存待處理紀錄；Gmail 寫入結果不明時，下次先重新讀取狀態，必要時重試同一個可重複操作。未完成的批次會跨日接續，避免已移到垃圾桶的信漏報。

Telegram 傳送前也保存紀錄。遇到 timeout 或服務錯誤而無法確定投遞結果時，批次回傳 503 並保留 `sending/uncertain`，**不盲目重送或聲稱完成**。管理者須檢查 Telegram 和資料庫紀錄後才能修復投遞狀態；目前沒有自動解除不明狀態的介面。明確拒絕（例如 HTTP 429）可於後续排程重試。OAuth／Telegram 配置錯誤會保留進度並回報失敗。

`保留` 防止這封信以後被自動過濾，不代表從垃圾桶還原。已在垃圾桶的信可直接在 Gmail 還原。
