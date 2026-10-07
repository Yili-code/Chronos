# Chronos — Telegram Task Assistant

**把中英文訊息變成代辦，在自己的 Telegram bot 管理期限與完成狀態，每天收到未完成清單。**

A self-hosted, single-user Telegram task assistant with Gemini-powered natural-language input, a Web dashboard, and a daily task digest.

[English setup and usage](README.en.md)

[![CI](https://github.com/Yili-code/Chronos/actions/workflows/ci.yml/badge.svg)](https://github.com/Yili-code/Chronos/actions/workflows/ci.yml)

[本機 Demo](docs/demo.md) · [本機啟動](docs/getting-started.md) · [指令與設定](docs/usage.md) · [雲端部署](docs/deployment.md) · [問題回報](https://github.com/Yili-code/Chronos/issues) · [貢獻指南](CONTRIBUTING.md)

## 適合誰

適合習慣用 Telegram、願意設定自己的 bot 與 Gemini key，並希望自行部署個人代辦工具的人。以 SQLite 在本機保存資料，也可選用 Cloud Run + Firestore。互動介面與儲存的代辦標題以英文為主，輸入接受中文與英文。

目前版本 `0.1.0`，仍在開發；Repository 尚未提供 License，也尚未發布 GitHub Release。請先釐清授權再採用於再散布或程式碼貢獻。沒有公開共用 bot 或線上 Demo；可先跑 [不用 API key 的本機 Web 示範](docs/demo.md)，其中任務為合成資料，僅展示清單與完成操作。

## 現在能做什麼

| 功能 | 條件與範圍 |
| --- | --- |
| 自然語言新增、語意編輯代辦 | 需要可用的 Gemini API key；外部 AI 失敗時不新增或更動代辦。 |
| `/tasks`、`/done`、明確移除期限或分類 | 查詢與完成不需要 AI；Telegram 需要自己的 bot、chat ID 與 HTTPS webhook。 |
| 每日未完成清單 | 預設 `Asia/Taipei` 08:00；需要持續運行的服務或外部 scheduler，不是逐筆到期提醒。 |
| Web 儀表板 | 查看、新增及完成代辦；沒有 Gemini key 時可先看空白介面。 |
| SQLite / Firestore | 本機預設 SQLite；Firestore 是選用的雲端設定。 |
| Study 模組 | 預設關閉；包含特定 NTOU / TronClass 課表、教材、作業與筆記流程，需額外設定，尚非跨校即用功能。 |

這是個人用途，沒有多使用者帳號隔離。`#Chronos` 是代辦分類標籤，不會追蹤 Git repository。AI 文字會送到 Gemini；詳細資料流及失敗行為見 [指令參考](docs/usage.md)。

## 本機介面預覽

![Chronos Web task dashboard with two synthetic tasks, a due date, a project tag and Complete buttons](docs/images/web-demo.png)

實際 Web 畫面，使用合成任務；不是使用者資料，也不是 AI 解析或 Telegram 投遞證據。[執行相同的本機示範](docs/demo.md)。

## 從一筆代辦開始

配置完成後，在**自己的 bot** 依序輸入：

```text
明天 17:00 完成報告 #Chronos
/tasks
/edit 1 移除期限
/tasks
/done 1
```

這是輸入示例，AI 產生的英文標題與相對日期依當時環境而定。先用 `/tasks` 確認任務的位置，編輯後再查一次；清單按期限排序，`1` 是當下位置。完整的 Retry、清除確認與 Study 指令見 [使用說明](docs/usage.md)。

## 快速啟動：Windows PowerShell

需要 Git 與 Python 3.11 以上。在尚未下載專案時執行：

```powershell
git clone https://github.com/Yili-code/Chronos.git
cd Chronos
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\python.exe -m uvicorn chronos.main:app --host 127.0.0.1 --port 8000
```

開啟 <http://127.0.0.1:8000>。新環境應顯示 **No open tasks.**；<http://127.0.0.1:8000/health> 應回傳 `{"status":"ok"}`。Telegram 設定可以先留空；**自然語言新增仍需要在 `.env` 填入 Gemini key 並重啟**。

下一步依 [完整啟動指南](docs/getting-started.md) 建立第一筆任務、接上 Telegram；同頁提供 macOS / Linux、Docker 與常見問題。公開 Web 前設定自己的密碼並使用 HTTPS。

## 文件與驗證

- [本機 Web 示範](docs/demo.md)：合成資料、暫存 SQLite，不呼叫外部服務。
- [本機啟動與第一筆代辦](docs/getting-started.md)：安裝、預期畫面、設定與故障排除。
- [Gemini 與 Telegram 指令參考](docs/usage.md)：自然語言、動態位置、編輯重試及資料保存。
- [Cloud Run + Firestore](docs/deployment.md)：選用部署，需要自己的 GCP project，可能產生費用。
- [Study 文件入口](docs/README.md)：區分目前入口、歷史設計與驗證紀錄。
- [驗證報告](TEST_REPORT.md)：既有測試與 production 證據，並列有尚未部署項目；CI 通過不代表雲端已更新。

程式在 `chronos/`，Python 測試在 `tests/`，Study Chrome extension 在 `chrome-extension/`，部署及驗證工具在 `scripts/`。部分 Study 工具含維護者專用設定，閱讀後再執行。

## 回饋與參與

安裝卡住或代辦行為不符預期，請[回報可重現問題](https://github.com/Yili-code/Chronos/issues/new/choose)，附環境、步驟與清除憑證後的錯誤。文件更正與真實使用情境也很有幫助；程式碼貢獻先讀 [CONTRIBUTING](CONTRIBUTING.md) 的授權狀態。若專案對你有用，可以 Star 以便日後找到；關注版本更新可在 GitHub 使用 Watch 的 Releases 選項，首個 Release 仍待發布。
