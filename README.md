# Chronos — Telegram Task Assistant

**把中英文訊息變成代辦，在自己的 Telegram bot 管理期限與完成狀態，每天收到未完成清單。**

A self-hosted, single-user Telegram task assistant with Gemini-powered natural-language input, and a daily task digest.

[English setup and usage](README.en.md)

[![CI](https://github.com/Yili-code/Chronos/actions/workflows/ci.yml/badge.svg)](https://github.com/Yili-code/Chronos/actions/workflows/ci.yml)

[本機啟動](docs/getting-started.md) · [指令與設定](docs/usage.md) · [雲端部署](docs/deployment.md) · [問題回報](https://github.com/Yili-code/Chronos/issues) · [貢獻指南](CONTRIBUTING.md)

## 適合誰

適合習慣用 Telegram、願意設定自己的 bot 與 Gemini key，並希望自行部署個人代辦工具的人。以 SQLite 在本機保存資料，也可選用 Cloud Run + Firestore。互動介面與儲存的代辦標題以英文為主，輸入接受中文與英文。

目前版本 `0.1.0`，仍在開發；Repository 尚未提供 License，也尚未發布 GitHub Release。請先釐清授權再採用於再散布或程式碼貢獻。沒有公開共用 bot 或線上 Demo。

## 現在能做什麼

| 功能 | 條件與範圍 |
| --- | --- |
| 自然語言新增、語意編輯代辦 | 需要可用的 Gemini API key；外部 AI 失敗時不新增或更動代辦。 |
| `/tasks`、`/done`、明確移除期限或分類 | 查詢與完成不需要 AI；Telegram 需要自己的 bot、chat ID 與 HTTPS webhook。 |
| 編輯撤回 | 修改成功後按 Undo，或 `/undo` 還原最近一次 edit；不撤回完成。 |
| 期限提醒 | 有時間：前 24 小時及 1 小時；只有日期：前一天及當天 09:00。 |
| 每日未完成清單 | 預設 `Asia/Taipei` 08:00；需要持續運行的服務或外部 scheduler，不是逐筆到期提醒。 |
| 學習行事曆（選用） | 參考課表與 Google Calendar 提出時段，按 Allow 才建立活動；結束後追蹤進度。需另外授權，規則與驗證界線見 [說明](docs/learning-calendar.md)。 |
| Gmail 郵件整理 | 選用；07:00 過濾明確促銷並移到垃圾桶、Telegram 摘要與按鈕／回覆新增任務。需獨立 OAuth，無寄信入口。[授權與啟用](docs/gmail.md)。 |
| SQLite / Firestore | 本機預設 SQLite；Firestore 是選用的雲端設定。 |
| Study 模組 | 預設關閉；保留特定 NTOU / TronClass 課表、進度、作業與公告。沒有教材下載、PDF AI 摘要或筆記匯出。 |

這是個人用途，沒有多使用者帳號隔離。`#Chronos` 是代辦分類標籤，不會追蹤 Git repository。AI 文字會送到 Gemini；詳細資料流及失敗行為見 [指令參考](docs/usage.md)。

## TronClass 每日作業掃描

可選用的 Cloud Run Job 會在每天 06:00（`Asia/Taipei`）以新 CAS session 讀取 `1151` 學期七門追蹤課程的作業清單，不需要電腦開機。它不繳交作業、不標記已讀，也不產生 draft。

- 同一作業只在首次出現時通知，附 `Open in TronClass` 與 `Edit task` 按鈕。
- 只納入 TronClass 明確顯示未繳交且尚未逾期的作業；過期項目保存 baseline，不建立任務。已繳交或繳交狀態不明的項目不納入。
- 正常但沒有新作業時會通知；相同失敗第一次安靜記錄、連續第二次才通知一次。
- Telegram 內可輸入 `/calendar` 查看與編輯掃描期間，例如 `/calendar disable 2026-10-25`、`/calendar term 2026-09-07 2027-01-31`。

部署 isolated Job 後，明確啟用 schedule：

```powershell
.\scripts\deploy_tronclass_assignment_scan.ps1
.\scripts\deploy_tronclass_assignment_scan.ps1 -ExecuteNow
.\scripts\deploy_tronclass_assignment_scan.ps1 -EnableSchedule
```

Job 使用獨立的 `chronos-collector` service account，只取得 CAS、Telegram 與 Firestore 所需權限；公開 Chronos service 不取得學校帳密。

## 期限倒推與拆解

`/help backward` 查看用法；`/backward 下週五要交資料庫報告` 先提出步驟、估時與工作時段，確認後才建立子任務與 Calendar 活動。
`/backward #42` 沿用既有目標，`/backward progress 42` 查看進度。容量不足會顯示缺口，不硬塞行程。
沿用既有 Calendar 授權及作息；日期精度、緩衝與恢復方式見 [Backward Planning](docs/backward-planning.md)。

## 從一筆代辦開始

配置完成後，在**自己的 bot** 依序輸入：

```text
明天 17:00 完成報告 #Chronos
/tasks
/edit 1 移除期限
/tasks
/done 1
```

這是輸入示例，AI 產生的英文標題與相對日期依當時環境而定。先用 `/tasks` 確認任務的位置，編輯後再查一次；清單按期限排序，`1` 是當下位置。課程範圍確認任務會集中顯示於下方的 `Course scope` 區；各區保留原清單編號，因此可能跳號，`/edit`、`/done` 請使用顯示的編號。完整的 Retry、清除確認與 Study 指令見 [使用說明](docs/usage.md)。

## 快速啟動：Windows PowerShell

需要 Git 與 Python 3.11 以上。在尚未下載專案時執行：

```powershell
git clone https://github.com/Yili-code/Chronos.git
cd Chronos
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install uv==0.12.3
.\.venv\Scripts\uv.exe sync --locked --extra dev
if (!(Test-Path .env)) { Copy-Item .env.example .env }
.\.venv\Scripts\uv.exe run uvicorn chronos.main:app --host 127.0.0.1 --port 8000
```

檢查服務：<http://127.0.0.1:8000/live> 應回傳 `{"status":"live"}`，<http://127.0.0.1:8000/ready> 應回傳 `status: ready`。Telegram 設定可以先留空；**自然語言新增仍需要在 `.env` 填入 Gemini key 並重啟**。

下一步依 [完整啟動指南](docs/getting-started.md) 建立第一筆任務、接上 Telegram；同頁提供 macOS / Linux、Docker 與常見問題。公開服務時使用 HTTPS。

## 文件與驗證

- [本機啟動與第一筆代辦](docs/getting-started.md)：安裝、預期畫面、設定與故障排除。
- [Gemini 與 Telegram 指令參考](docs/usage.md)：自然語言、動態位置、編輯重試及資料保存。
- [Cloud Run + Firestore](docs/deployment.md)：選用部署，需要自己的 GCP project，可能產生費用。
- [Study 課程追蹤](docs/study.md)：支援範圍、明確排除項目與啟用方式。
- [驗證報告](TEST_REPORT.md)：既有測試與 production 證據，並列有尚未部署項目；CI 通過不代表雲端已更新。

程式在 `chronos/`，Python 測試在 `tests/`，Study Chrome extension 在 `chrome-extension/`，部署及驗證工具在 `scripts/`。部分 Study 工具含維護者專用設定，閱讀後再執行。

## 回饋與參與

安裝卡住或代辦行為不符預期，請[回報可重現問題](https://github.com/Yili-code/Chronos/issues/new/choose)，附環境、步驟與清除憑證後的錯誤。文件更正與真實使用情境也很有幫助；程式碼貢獻先讀 [CONTRIBUTING](CONTRIBUTING.md) 的授權狀態。若專案對你有用，可以 Star 以便日後找到；關注版本更新可在 GitHub 使用 Watch 的 Releases 選項，首個 Release 仍待發布。
