# Cloud Run + Firestore 部署

Cloud Run 使用 Firestore 保存代辦與 Telegram update receipts，不依賴 container 的暫存檔案系統。Gemini API key、Telegram bot token、Telegram chat ID、webhook secret、Web password 與 scheduler secret 由 Secret Manager 注入；secret 不會寫入 image 或 repository。

這是選用部署路徑，會建立或修改 GCP 資源並可能產生費用。使用自己的 project、bot 與憑證；維護者的私有 instance 不是公開 Demo。

以下指令於 repository 根目錄執行。先安裝 [Google Cloud CLI](https://cloud.google.com/sdk/docs/install) 並登入：

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

首次啟用 production 課後追蹤時，明確加入 `-EnableStudyTracking`。之後部署會保留已啟用狀態；只有加入 `-DisableStudyTracking` 才會關閉：

```powershell
pwsh -File .\scripts\deploy_cloud_run.ps1 -ProjectId YOUR_PROJECT_ID -EnableStudyTracking
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

## Web 安全

設定 `CHRONOS_WEB_PASSWORD` 後，Web 與 API 會啟用 Basic Auth。若公開部署，必須設定此值，並只允許 HTTPS。Telegram webhook 另以 secret header 驗證。


通用部署腳本只建立每日代辦 job。Study 的課表、session publisher 與部分驗證腳本包含維護者專用設定，不能直接當成通用雲端安裝步驟。

回到 [本機啟動](getting-started.md) 或 [README](../README.md)。
