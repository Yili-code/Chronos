# Study 課程追蹤

Study 是 owner-only 的選用模組，預設關閉。它保留四項能力：

- 依固定課表詢問當日課程進度，最多提醒兩次；
- 保存回覆並建立複習代辦；
- 保存及同步已觀察到的作業與公告；
- 由 `/prepare <assignment-id>` 對已保存的作業文字建立可編輯草稿。

Chrome extension 的本機 receiver 只接受 redacted page observation、作業說明與公告。
作業附件僅保存畫面上可見的 filename 與 source ID；`/prepare` 不讀附件。

## 明確不包含

Chronos 不會自動或手動下載 TronClass 教材，不保存課程 PDF，不把教材傳給
Gemini，不建立教材筆記，也不提供 `/notes`、`/note` 或 `/export`。Extension
沒有 `downloads` 或 `cookies` 權限，也沒有登入狀態的 cloud handoff。

這個邊界的實際價值是把兩種風險分離：課程進度與作業文字是 bounded metadata
workflow；教材下載與模型處理則涉及 authenticated content、長期保存、token cost
與更高的 privacy burden。移除後，啟用 Study 不會授權教材存取。

## 啟用課程追蹤

本機 `.env`：

```dotenv
CHRONOS_ENABLE_STUDY_TRACKING=true
```

Cloud Run 透過 `scripts/deploy_cloud_run.ps1 -EnableStudyTracking` 保留既有
`chronos-course-progress` Scheduler。啟用只代表課表 prompt、calendar、作業與公告
流程可執行，不代表 Chrome extension、Gemini 或 Telegram 的 live outcome 已驗證。

## 作業準備

`/prepare` 是明確觸發、文字限定的工作。獨立 worker 必須另外啟用：

```powershell
.\.venv\Scripts\python.exe -m chronos.run_preparation_companion --check
.\.venv\Scripts\python.exe -m chronos.run_preparation_companion --enable --free-tier-confirmed --watch
```

設定 `CHRONOS_PREPARATION_GEMINI_MODEL`、
`CHRONOS_PREPARATION_DAILY_REQUEST_LIMIT` 與
`CHRONOS_PREPARATION_DAILY_TOKEN_LIMIT`。這些是本機 safety limits，不是供應商
quota 或免費方案保證。

## Chrome extension

1. 在 `chrome://extensions` 啟用 Developer mode。
2. Load unpacked，選擇 `chrome-extension/`。
3. 取得 extension ID。
4. 啟動 loopback receiver：

```powershell
.\.venv\Scripts\python.exe -m chronos.local_observation_server --extension-id YOUR_32_CHARACTER_EXTENSION_ID
```

Popup 可明確保存目前作業或公告；背景公告監測必須由 owner 按下啟用。所有
receiver endpoints 都綁定 `127.0.0.1`，且只接受設定的 extension origin。
