# Chronos

以 Telegram 為主、Web 為輔的個人專案與代辦助理。

## 功能

- 掃描 `CHRONOS_PROJECTS_ROOT` 下的 Git repository（Windows 預設為使用者的 `Documents/Developing`）
- 顯示 branch、未 commit 變更與最後一筆 commit
- 使用 `GitHub token` 取得 repository 狀態
- 透過 Telegram 自然語言新增、查詢、完成及延期代辦
- 每天 `Asia/Taipei` 08:00 傳送未完成代辦
- Web 儀表板提供專案與代辦概覽
- SQLite 儲存代辦，不依賴外部資料庫

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

先編輯 `.env`；路徑使用 `/`，例如 `C:/Users/User/Documents/Developing`。
Telegram 與 GitHub 設定可先留空，以啟動本機 Web 功能。
專案包含 `tzdata` 依賴，供 Windows 使用 `Asia/Taipei` 時區。

## 外部 AI 設定

Web 與 Telegram 的自然語言新增、Telegram 延期共用外部 AI，使用供應商的 OpenAI-compatible Chat Completions 介面，不啟動本地模型。

在既有 `.env` 加入以下設定（不要覆蓋原有內容）：

```dotenv
CHRONOS_AI_BASE_URL=https://你的供應商提供的API根網址/v1
CHRONOS_AI_API_KEY=你的API金鑰
CHRONOS_AI_MODEL=供應商提供的模型名稱
CHRONOS_AI_TIMEOUT=30
```

根網址依供應商文件填寫；程式會附加 `/chat/completions`，請勿填入完整 endpoint。模型需能依指示回傳 JSON。換供應商或模型只需更新設定並重啟，無需管理本機模型或 GPU。

自然語言文字與目前時間會送至指定供應商；不會附帶 repository 內容或整份代辦清單。未設定、逾時或格式錯誤時會顯示錯誤且不寫入代辦，不會退回規則解析。代辦查詢、完成、專案掃描與每日提醒仍可獨立使用。API 費用依供應商計算。

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
新增 明天 17:00 完成報告 #Chronos
代辦
完成 3
延期 3 到週五 10:00
專案
```

設定 `CHRONOS_TELEGRAM_CHAT_ID` 後，其他 chat 無法操作 bot。

## GitHub 設定

公開 repository 的本機狀態不需要 token。若要補充 GitHub repository metadata，建立最小權限的 fine-grained token，並設定：

```dotenv
CHRONOS_GITHUB_TOKEN=github_pat_...
```

token 只需讀取目標 repository 的 Metadata。不要將 `.env` commit。

## Docker

`compose.yaml` 已將本機開發目錄以唯讀方式掛載至 container：

```bash
docker compose up -d --build
```

Docker Desktop 需使用 Linux containers。掛載來源取自 `.env` 的 `CHRONOS_PROJECTS_ROOT`；若帳號或路徑不同，修改該變數即可。

## Web 安全

設定 `CHRONOS_WEB_PASSWORD` 後，Web 與 API 會啟用 Basic Auth。若公開部署，必須設定此值，並只允許 HTTPS。Telegram webhook 另以 secret header 驗證。

## 測試

```powershell
.\.venv\Scripts\python.exe -m pytest
```
