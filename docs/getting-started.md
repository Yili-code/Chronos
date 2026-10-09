# 本機啟動與第一筆代辦

Chronos 是單一使用者的 Telegram task assistant，Web 是輔助介面。先用本機 SQLite 確認能啟動，再配置 Gemini 與 Telegram。無 API key 時可以開啟空白儀表板與健康檢查，但不能以自然語言建立代辦。

## Windows PowerShell

需要 Git、Python 3.11 以上；CI 使用 Python 3.11 與 3.12。以下從尚未下載專案開始；已在專案根目錄者跳過前兩行。

```powershell
git clone https://github.com/Yili-code/Chronos.git
cd Chronos
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv==0.12.3
.\.venv\Scripts\uv.exe sync --locked --extra dev
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\uv.exe run uvicorn chronos.main:app --host 127.0.0.1 --port 8000
```

## macOS / Linux

```bash
git clone https://github.com/Yili-code/Chronos.git
cd Chronos
python3 -m venv .venv
.venv/bin/python -m pip install uv==0.12.3
.venv/bin/uv sync --locked --extra dev
test -f .env || cp .env.example .env
.venv/bin/uv run uvicorn chronos.main:app --host 127.0.0.1 --port 8000
```

開啟 <http://127.0.0.1:8000>，預期看到 **Open tasks** 與 **No open tasks.**。在另一個 PowerShell 視窗執行：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/live
Invoke-RestMethod http://127.0.0.1:8000/ready
Invoke-RestMethod http://127.0.0.1:8000/api/tasks
```

`/live` 回傳 `status: live`，`/ready` 回傳 `status: ready`，新的 SQLite 資料庫回傳空清單。這只證明 process、設定與資料庫可用，不代表 Gemini、Telegram 或雲端已連通。`chronos.db` 是本機資料，已被 Git 忽略；停止服務後可備份該檔案。

## Gemini：新增第一筆代辦

在 [Google AI Studio](https://aistudio.google.com/apikey) 取得自己的 API key，編輯 `.env` 既有欄位：

```dotenv
CHRONOS_GEMINI_API_KEY=你的金鑰
CHRONOS_GEMINI_MODEL=gemini-3.8-flash
```

模型名稱須在你的帳號可用；可查 [Google 官方模型清單](https://ai.google.dev/gemini-api/docs/models)。費用、配額與區域支援依供應商規定，專案沒有保證免費使用。

停止並重新啟動服務，在 Web 輸入 `明天 17:00 完成報告 #Chronos`，按 **Add**。成功後應出現英文標題、期限與分類；請確認解析出的日期，再按 **Complete**。相對日期依執行當下時間與 `CHRONOS_TIMEZONE` 解析。這是可嘗試的輸入例，不保證每次模型輸出相同標題。

輸入文字與目前時間會送至 Gemini；不會附帶整份代辦清單。標題儲存為精簡英文，新增代辦的原始中文不另行保存。沒有設定、解析失敗或服務錯誤時不建立代辦，沒有離線規則解析替代方案。

## Telegram：自己的 bot 與 chat

1. 在 [BotFather](https://t.me/BotFather) 使用 `/newbot` 取得 token；先傳 `/start` 給新 bot。
2. 在尚未配置 webhook 的新 bot 上，依 [Telegram getUpdates 文件](https://core.telegram.org/bots/api#getupdates) 查自己的 `message.chat.id`。token 屬於憑證，不要將含 token 的 URL 放入 Issue、截圖或公共工具。已有 webhook 時不能同時使用 getUpdates；不要為了示範移除 production webhook。
3. 在 `.env` 填入以下四項，設定自己的 HTTPS 公開入口。僅 `127.0.0.1` 無法讓 Telegram 投遞 webhook。

```dotenv
CHRONOS_TELEGRAM_BOT_TOKEN=你的token
CHRONOS_TELEGRAM_CHAT_ID=你的整數chat_id
CHRONOS_TELEGRAM_WEBHOOK_SECRET=足夠長的隨機字串
CHRONOS_PUBLIC_BASE_URL=https://你的公開入口
```

公開 Web 前另設定 `CHRONOS_WEB_PASSWORD`，只允許 HTTPS。不要把範例 bot 或維護者帳號當成可共用服務。重啟時程式會嘗試註冊 `/telegram/webhook`；註冊失敗會記錄錯誤，Web 成功啟動不等於 webhook 成功。

在自己的 bot 依序傳送：

```text
明天 17:00 完成報告 #Chronos
/tasks
/edit 1 移除期限
/tasks
/done 1
```

先用 `/tasks` 確認位置 1 是剛建立的任務；多筆代辦會依期限重新排序。`/edit` 移除期限是確定性操作，不需 AI；其他語意編輯可能需要 Gemini。每天 08:00 的提醒需要服務持續運行或外部 scheduler，並不是每筆任務到期時的即時通知。完整行為見 [指令參考](usage.md)。

## Docker

先依上方建立、編輯 `.env`，再於根目錄執行：

```bash
docker compose up -d --build
docker compose logs --tail=50 chronos
```

需 Docker Compose，Windows Docker Desktop 使用 Linux containers。開啟同一個本機網址；資料保存在 `chronos-data` named volume。`docker compose down` 停止服務並保留 volume；`down -v` 會刪除資料，不要當成一般重新啟動方式。

## 常見卡點

| 現象 | 下一步 |
| --- | --- |
| `No module named ...` | 確認用 `.venv` 的 Python，重新執行安裝指令。 |
| Web 能開，Add 卻失敗 | 檢查 Gemini key、可用模型與配額；修改 `.env` 後重啟。 |
| Web 回傳 401 | 使用 `CHRONOS_WEB_USERNAME`（預設 `chronos`）與自己設定的密碼。 |
| Telegram 不回覆 | 確認四項設定、HTTPS webhook、chat ID，以及是否封鎖 bot。 |
| 8000 已被占用 | 改用 `--port 8001`，同時改開 `http://127.0.0.1:8001`。 |
| Study 沒啟動 | 預設關閉，且包含專用課表與資料來源；不是一般代辦的必要步驟。 |

雲端部署會建立資源，請先閱讀 [部署指南](deployment.md)。回報安裝問題時附作業系統、Python 版本、執行步驟與移除憑證後的錯誤，見 [貢獻指南](../CONTRIBUTING.md)。
