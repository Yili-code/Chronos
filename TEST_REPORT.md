# Chronos 驗證報告

更新日期：2026-10-07（Asia/Taipei）

## 結果

- `621 passed`，沒有測試失敗；warnings 為既有 dependency deprecations 與 pytest cache 權限提示
- Python compilation、PowerShell deployment syntax、`git diff --check` 通過
- Cloud Run `chronos-00014-mrj` Ready，承接 100% traffic
- Production `/health` 回傳 `ok`
- 未授權 Web 為 `401`，正確帳密為 `200`
- 已移除的 `/api/projects` 在 production 為 `404`
- 未授權 scheduler request 為 `403`

## Pending release（尚未部署）

- Telegram 與 Web 互動介面統一為英文；自然語言仍接受中文與英文
- Gemini 將標題正規化為精簡英文 action phrase，並將一般分類轉為英文 lowercase kebab-case
- `/tasks` 使用目前未完成清單的動態位置 `1..n`，永久 database ID 不顯示
- `/done` 與 `/edit` 在 transaction 內將動態位置解析為永久 ID；保存後的 edit retry 仍綁定同一永久 ID，不受清單重新排序影響
- `/edit <position> <instruction>` 接受中英文自然語言；移除日期、移除分類及 tag abbreviation preference 先走 deterministic path，其他語意改寫才使用 AI
- AI update service 不可用時不變更 task，持久保存原 command，並提供 contextual Retry/Cancel buttons；retry 成功或取消後才移除 pending edit
- 更新摘要與最新 task list 分成兩則 Telegram 訊息，逐則保存 delivery progress；第二則失敗時只補送未確認部分
- task title、完整日期時間與 tag 分層顯示；長多字 tag 使用顯示縮寫，例如 `computer-architecture` → `#CA`，canonical value 不變
- 使用者可見的 task status/error copy 以英文為主，provider name 與 automatic-retry 細節只留在 logs
- `/clear` 必須再按 English inline button 確認，才會刪除 SQLite 或 Firestore 中所有 open 與 completed task records；Cancel 不變更資料
- Telegram webhook 已接受 `callback_query`，callback mutation 與 update receipt 在同一 transaction 中處理，重送不會重複清除
- `/postpone` 與中文 commands 已移除；`/start` 保留並顯示 `/help` 內容
- 兩項新功能完成 local validation 後分為兩個 commit/push；production 部署與 live verification 待執行

## Production integration

- Gemini 成功將「2026/09/30 09:00 Chronos 雲端部署驗證」解析成 Asia/Taipei 時間
- Gemini 的 `429`、`5xx`、timeout 與 transport failure 會在 30 秒總限制內最多嘗試 3 次；永久錯誤不重試，使用者訊息依驗證、模型、額度、服務繁忙與網路問題分類
- Production 成功建立 synthetic task `#2`，驗證 Gemini → Firestore 後已立即完成清理
- Firestore 成功保存該任務；production 查詢可讀回同一筆資料
- Telegram webhook 已註冊到 Cloud Run，chat ID 驗證為 private chat
- Production `/help` webhook 回傳 `200`；新版自然語言範例與 slash command 格式已實際送至 Telegram
- Cloud Scheduler job 已啟用，時區為 `Asia/Taipei`，每天 08:00 執行
- 解除 Telegram 封鎖後，手動執行 Cloud Scheduler；production `/internal/daily` 在 `chronos-00008-lk2` 回傳 `200`，每日清單完成實際投遞
- Cloud Run 使用 `chronos-runtime` service account；Firestore database 位於 `asia-east1` 且啟用 delete protection
- Gemini key、Telegram token/chat ID、Web password、webhook secret、scheduler secret 均由 Secret Manager 注入
- Telegram transport error 已改為安全錯誤，不會把 bot token 帶進新 log；live audit 確認近期 log 不含 token

## 已移除

Repository project tracking 已完整移除，包括 Web panel、`/api/projects`、Telegram 指令、Git/GitHub scanner、環境變數、Docker bind mount、deployment secrets、tests 與文件。代辦的 `#project` 分類標籤保留；它只是 task metadata，不會讀取或追蹤 Git repository。

## 已解除的外部限制

Telegram 曾對 `@Chronos_assistant_yili001_bot` 回覆 `Forbidden: bot was blocked by the user`。使用者已解除封鎖；真實 `/start`、`/help` 與 Cloud Scheduler 每日清單均已成功回覆，不需重新部署。
