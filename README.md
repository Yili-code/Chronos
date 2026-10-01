# Chronos

以 Telegram 為主、Web 為輔的個人代辦助理。

## 功能

- 透過 Telegram 自然語言新增、查詢、完成及延期代辦
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

Web 與 Telegram 的自然語言新增、Telegram 延期共用 Gemini API，不啟動本地模型。程式呼叫 Gemini 原生 `generateContent` endpoint，要求 JSON response 並再次驗證輸出 schema。

在既有 `.env` 加入以下設定（不要覆蓋原有內容）：

```dotenv
CHRONOS_GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
CHRONOS_GEMINI_API_KEY=你的Gemini API金鑰
CHRONOS_GEMINI_MODEL=gemini-3.8-flash
CHRONOS_AI_TIMEOUT=30
```

API key 可由 Google AI Studio 建立。若模型名稱在帳號或地區不可用，更新 `CHRONOS_GEMINI_MODEL` 後重啟即可。

自然語言文字與目前時間會送至 Gemini；不會附帶整份代辦清單。未設定、逾時或格式錯誤時會顯示錯誤且不寫入代辦，不會退回規則解析。代辦查詢、完成與每日提醒仍可獨立使用。API 費用與限制依 Gemini 帳號方案計算。

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
/done 3
/postpone 3 週五 10:00
```

除了管理指令外，直接傳送自然語言就會新增一筆代辦，不必先輸入「新增」。
`#Chronos` 是選填的分類標籤；`/done` 與 `/postpone` 後方的數字是 `/tasks` 清單顯示的項目編號。
中文的 `代辦`、`完成 3` 與 `延期 3 到週五 10:00` 也能使用。

設定 `CHRONOS_TELEGRAM_CHAT_ID` 後，其他 chat 無法操作 bot。

Webhook 會驗證 JSON 結構與整數 `update_id`，合法的非文字更新會略過。每筆文字更新的代辦變更與處理紀錄會一起儲存在 SQLite；重送同一 update 不會重複新增、完成或延期，重啟後仍有效。回覆失敗時，後續重送會重試原始回覆。若 Telegram 已收到回覆而程式尚未記錄送達就中斷，回覆文字仍可能重複，但代辦不會重複變更。去重紀錄目前不會自動清除。

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
