# Chronos

以 Telegram 為主、Web 為輔的個人代辦助理。

## 功能

- 透過 Telegram 自然語言新增、查詢、完成及改期代辦
- 每天 `Asia/Taipei` 08:00 傳送未完成代辦
- Web 儀表板提供代辦概覽
- 本機使用 SQLite；Cloud Run 使用 Firestore

## 啟動

需要 Python 3.11 以上版本。

在專案根目錄使用 Windows PowerShell：

```powershell
# 僅在尚未建立 .env 時複製，避免覆寫已填好的設定。
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m uvicorn chronos.main:app --reload --host 127.0.0.1 --port 8000
```

開啟 `http://127.0.0.1:8000`。

先編輯 `.env`。Telegram 設定可先留空，以啟動本機 Web 功能。
專案包含 `tzdata` 依賴，供 Windows 使用 `Asia/Taipei` 時區。

## Gemini 設定

Web 與 Telegram 的自然語言新增、Telegram 改期共用 Gemini API，不啟動本地模型。程式呼叫 Gemini 原生 `generateContent` endpoint，要求 JSON response 並再次驗證輸出 schema。

在既有 `.env` 加入以下設定（不要覆蓋原有內容）：

```dotenv
CHRONOS_GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
CHRONOS_GEMINI_API_KEY=你的Gemini API金鑰
CHRONOS_GEMINI_MODEL=gemini-3.8-flash
CHRONOS_AI_TIMEOUT=30
```

API key 可由 Google AI Studio 建立。若模型名稱在帳號或地區不可用，更新 `CHRONOS_GEMINI_MODEL` 後重啟即可。

自然語言文字與目前時間會送至 Gemini；不會附帶整份代辦清單。輸入可使用中文或英文，儲存時會正規化成自然、精簡的英文 action phrase；一般分類標籤轉成英文 lowercase kebab-case，品牌、正式專案名與技術術語保留原名。原始中文不另行保存。未設定、逾時或格式錯誤時會顯示錯誤且不寫入代辦，不會退回規則解析。代辦查詢、完成與每日提醒仍可獨立使用。API 費用與限制依 Gemini 帳號方案計算。

Gemini 遇到 `429`、`5xx`、timeout 或 transport failure 時，會在同一個總 timeout 內最多嘗試 3 次，退避 1 秒、2 秒。`401/403`（金鑰或權限）、`404`（模型）、`429`（請求限制或額度）、`5xx`（服務暫時繁忙）與網路錯誤會回覆不同訊息；所有失敗都維持 no-write，不會建立或修改代辦。

## Telegram 設定

1. 在 Telegram 對 `@BotFather` 執行 `/newbot`，取得 bot token。
2. 先傳訊息給新 bot，再以 `getUpdates` 取得自己的 `chat.id`。
3. 將以下內容填入 `.env`：

```dotenv
CHRONOS_TELEGRAM_BOT_TOKEN=...
CHRONOS_TELEGRAM_CHAT_ID=...
CHRONOS_TELEGRAM_WEBHOOK_SECRET=一組足夠長的隨機字串
CHRONOS_PUBLIC_BASE_URL=https://你的公開網址
```

啟動時會自動將 webhook 設為 `CHRONOS_PUBLIC_BASE_URL/telegram/webhook`。公開網址必須使用 HTTPS。

支援的訊息：

```text
明天 17:00 完成報告 #Chronos
/tasks
/done 1
/edit 1 改成週五 10:00 交 final report 並移除專案
/clear
```

除了 slash commands 外，直接傳送中英文自然語言就會新增一筆代辦。Telegram 與 Web 的互動文字統一使用英文。
`/tasks` 依「期限最早、無期限最後、同期限較早建立者優先」排序，並將目前未完成代辦動態編為 `1..n`；永久 database ID 不會顯示。`/done` 與 `/edit` 使用這個當下位置，操作後會回覆結果及更新後清單。不存在的位置會顯示錯誤及最新清單。每天 08:00 的清單使用同一格式。
`/edit <position> <instruction>` 接受中文或英文自然語言，可同時修改標題、期限與分類，也能明確移除期限或分類；未提及的欄位會保留，儲存標題仍為精簡英文 action phrase。任務改期與作業截止日期都統一使用 `/edit`；`/reschedule` 與 `/deadline` 不再支援。
舊的中文 commands 與 `/postpone` 不再支援；`/start` 與 `/help` 都會顯示英文使用說明。
`/clear` 先顯示確認訊息；只有按下 **Delete all tasks** 才會刪除所有 open 與 completed task records，按 **Cancel** 不會變更資料。Telegram update receipts 與內部 ID counter 不在清除範圍內，以維持 webhook idempotency 與 ID 唯一性。

### Study 指令範例

作業固定 ID 會直接出現在作業通知，例如 `新作業 #27`。這個 `27` 只供 `/prepare` 與 `/draft` 使用；修改截止日期時，先用 `/tasks` 找到當下順位，再用 `/edit`。`/assignment`、`/exam` 與 `/exams` 不再支援。

`/classday` 接受中文或英文自然語言，用於確認特定課程在某天是否上課：

```text
/classday 10/07 軟體工程不上課
```

筆記範例以「作業系統」課程為例。先列出該課程已保存的 notes，再複製回覆中的完整 64 字元 note ID：

```text
/notes 作業系統
/note <從上一個回覆取得的完整 note ID>
/export <同一個完整 note ID>
```

`/note` 讀取內容，`/export` 則傳送同一份 canonical note 的 Markdown 文件。Repository 不提供固定示範 ID，因為 note ID 是由實際內容計算，寫死的 ID 可能指向不存在或不同的筆記。

`/tasks` 與每日清單使用粗體 `Tasks` 標題，以空格分隔欄位。課程日期顯示為 `MM/DD`；課後回覆原文仍保存作為教材與摘要依據，但新建立的複習代辦會另外使用一次 Gemini 產生簡短英文 label，並在 Telegram 中以粗體顯示。若翻譯暫時失敗，進度仍會保存，複習代辦回退顯示原文。

設定 `CHRONOS_TELEGRAM_CHAT_ID` 後，其他 chat 無法操作 bot。

Webhook 會驗證 JSON 結構與整數 `update_id`，合法的非文字更新會略過。每筆 message 或 callback 的代辦變更與處理紀錄會一起儲存在 SQLite；動態位置會在同一 transaction 內解析成永久 ID，重送同一 update 不會重複新增、完成、修改、改期或清除，重啟後仍有效。回覆失敗時，後續重送會重試原始回覆。若 Telegram 已收到回覆而程式尚未記錄送達就中斷，回覆文字仍可能重複，但代辦不會重複變更。去重紀錄目前不會自動清除。

## Docker

```bash
docker compose up -d --build
```

Docker Desktop 需使用 Linux containers。SQLite 資料保存在 named volume `chronos-data`。

## Cloud Run + Firestore 部署

Cloud Run 使用 Firestore 保存代辦與 Telegram update receipts，不依賴 container 的暫存檔案系統。Gemini API key、Telegram bot token、Telegram chat ID、webhook secret、Web password 與 scheduler secret 由 Secret Manager 注入；secret 不會寫入 image 或 repository。

先安裝 Google Cloud CLI 並登入：

```powershell
gcloud auth login
gcloud auth application-default login
```

`.env` 需要本機保存以下兩項，部署腳本只會讀取值並送往 Secret Manager，不會 commit：

```dotenv
CHRONOS_TELEGRAM_BOT_TOKEN=...
CHRONOS_TELEGRAM_CHAT_ID=...
```

若 `.env` 已有 `CHRONOS_GEMINI_API_KEY`，部署會使用該 key；否則腳本會在目標 GCP project 建立一把只允許 Gemini API 的 dedicated key，再將 key string 寫入 Secret Manager，全程不輸出 key。

執行：

```powershell
pwsh -File .\scripts\deploy_cloud_run.ps1 -ProjectId YOUR_PROJECT_ID
```

部署腳本需要 PowerShell 7。重跑時會沿用既有的 Web password、webhook secret 與 scheduler secret；重新部署不等於旋轉憑證。

腳本會：

1. 啟用 Cloud Run、Cloud Build、Firestore、Secret Manager、API Keys、Gemini 與 Cloud Scheduler APIs。
2. 建立最小權限的 `chronos-runtime` service account。
3. 建立 Firestore Native `(default)` database，並啟用 delete protection。
4. 建立或取得 Chronos 專用 Gemini API key，再建立 Secret Manager secrets 與版本，授權 runtime identity 讀取。
5. 由目前 source build 並部署 Cloud Run。
6. 將 Cloud Run URL 設成 Telegram webhook。
7. 建立每天 `Asia/Taipei` 08:00 的 Cloud Scheduler job。
8. 驗證 `/health` 與 Telegram `getWebhookInfo`。

### 目前 production

- GCP project：`yili-chronos-prod`
- Cloud Run：<https://chronos-w42vzvnetq-de.a.run.app>
- Web username：`chronos`
- Telegram bot：`@Chronos_assistant_yili001_bot`

Web password 只保存在 Secret Manager。需要登入時讀取目前版本：

```powershell
gcloud secrets versions access latest --secret chronos-web-password --project yili-chronos-prod
```

若 Telegram 顯示 bot 已封鎖，請先在 bot 對話解除封鎖並按 **Start**。這是 Telegram account-side permission，解除後不需重新部署。

## Web 安全

設定 `CHRONOS_WEB_PASSWORD` 後，Web 與 API 會啟用 Basic Auth。若公開部署，必須設定此值，並只允許 HTTPS。Telegram webhook 另以 secret header 驗證。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest
```
