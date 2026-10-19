# Gemini 與 Telegram 指令參考

期限倒推：`/command 下週五要交資料庫報告`；既有目標：`/command #42`；進度：`/backward 42`。
確認前不新增任務或活動。日期精度、容量缺口與部分失敗恢復見 [Backward Planning](backward-planning.md)。

Telegram 的自然語言新增、Telegram 改期共用 Gemini API，不啟動本地模型。程式呼叫 Gemini 原生 `generateContent` endpoint，要求 JSON response 並再次驗證輸出 schema。

在既有 `.env` 加入以下設定（不要覆蓋原有內容）：

```dotenv
CHRONOS_GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
CHRONOS_GEMINI_API_KEY=你的Gemini API金鑰
CHRONOS_GEMINI_MODEL=gemini-3.5-flash-lite
CHRONOS_AI_TIMEOUT=30
```

API key 可由 [Google AI Studio](https://aistudio.google.com/apikey) 建立。若模型名稱在帳號或地區不可用，更新 `CHRONOS_GEMINI_MODEL` 後重啟即可。

自然語言文字與目前時間會送至 Gemini；不會附帶整份代辦清單。輸入可使用中文或英文，儲存時會正規化成自然、精簡的英文 action phrase；一般分類標籤轉成英文 lowercase kebab-case，品牌、正式專案名與技術術語保留原名。原始中文不另行保存。未設定、逾時或格式錯誤時會顯示錯誤且不寫入代辦，不會退回規則解析。代辦查詢、完成與每日提醒仍可獨立使用。API 費用與限制依 Gemini 帳號方案計算。

Gemini 遇到 `429`、`5xx`、timeout 或 transport failure 時，會在同一個總 timeout 內最多嘗試 3 次，退避 1 秒、2 秒。`401/403`（金鑰或權限）、`404`（模型）、`429`（請求限制或額度）、`5xx`（服務暫時繁忙）與網路錯誤會回覆不同訊息；所有失敗都維持 no-write，不會建立或修改代辦。

## Telegram 設定與指令

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
/ai_usage
/clear
```

除了 slash commands 外，直接傳送中英文自然語言就會新增一筆代辦。Telegram 與 Web 的互動文字統一使用英文。
AI 暫時不可用時，可用 `/add Review Graph Algorithms Quiz 1 #Graph` 新增一筆確定文字的任務；`/add` 原樣保存標題與可選的英文 tag，不解析標題中的日期，也不呼叫 AI；期限保持空白。自然語言新增仍維持 AI 失敗時不寫入的行為。

新增任務若沒有指定期限，期限保持空白，不自動補上三天後的日期。有明確指定的截止時間或只有日期的期限時保留原值；課程／事件日期與排定時間仍分別保存。既有任務的期限不受影響。

自然語言或 `/add` 新增成功後，會先顯示 `Task created` 任務卡，再自動送出包含新任務的最新清單，無須另外輸入 `/tasks`。清單維持期限排序與 `Course scope` 分區；若清單傳送失敗，重試只補送尚未送達的訊息，不會重複新增任務。

`Course scope` 是每次產生清單都會套用的顯示規則，涵蓋現有與之後新增或編輯的任務。`Check … class scope`、`Check … course scope` 不要求標題帶日期；日期即使獨立存於 `Course / event` 欄位，也會歸入下方區塊。系統產生的 `Check … MM/DD scope` 與中文確認上課範圍任務同樣適用。
若已知道完整新標題，可用 `/edit 1 title: "Watch Database Chapter 1 video"` 直接替換標題，保留期限、分類與其他時間資訊；此操作也不需要 AI。`Set the title to "..."` 與 `Translate the title to "..."` 接受明確提供的替換文字，並不自行翻譯。要求自動翻譯或摘要仍需要 AI。
`/tasks` 依「期限最早、無期限最後、同期限較早建立者優先」排序，並將目前未完成代辦動態編為 `1..n`；永久 database ID 不會顯示。`/done` 與 `/edit` 接受當下位置，但 mutation 會在 receipt transaction 內綁定永久 ID，因此清單重新排序不會讓已保存的 retry 改到另一筆 task。不存在的位置會顯示英文錯誤及最新清單。每天 08:00 的清單使用同一格式。
`/edit <position> <instruction>` 接受中文或英文自然語言，可同時修改標題、期限與分類，也能明確移除期限或分類；未提及的欄位會保留，儲存標題仍為精簡英文 action phrase。移除日期、移除分類與保存 tag abbreviation 等明確操作會走 deterministic path；其他語意改寫才呼叫 AI。任務改期與作業截止日期都統一使用 `/edit`；`/reschedule` 與 `/deadline` 不再支援。
AI update service 暫時不可用時，task 不會變更，原始 command 會持久保存，Telegram 會提供 **Retry editing Task n** 與 **Cancel saved edit for Task n**。Retry 依永久 task ID 執行；如果目標已完成或刪除，會 fail closed。成功更新分成兩則訊息：第一則只列出 changed fields，第二則才顯示更新後清單。兩則訊息分別保存 delivery progress，第二則失敗時不會主動重送已確認成功的第一則。
編輯失敗會保留 AI 層提供的安全錯誤原因（例如未設定、模型不可用、回應格式不合法或暫時性連線錯誤），不再一律稱為暫時無法使用。既有中文分類在只改標題時會原樣保留。
舊的中文 commands 與 `/postpone` 不再支援；`/start` 與 `/help` 只顯示 command categories，點選分類後才展開完整英文語法。也可直接輸入 `/help tasks`、`/help planning` 或 `/help assignments`；`/help study` 僅保留為相容別名。
`/clear` 先顯示確認訊息；只有按下 **Delete all tasks** 才會刪除所有 open 與 completed task records，按 **Cancel** 不會變更資料。Telegram update receipts 與內部 ID counter 不在清除範圍內，以維持 webhook idempotency 與 ID 唯一性。

`/ai_usage` 是 read-only command，顯示今天每種核心 AI operation 的 request、provider attempt 與 provider 回報 token totals。只保存 aggregate counters，不保存 prompt、response、API key 或 task text；未回報 token usage 時會明確顯示 `not reported`，不以字元數偽裝成精確 token 成本。

### Undo edit

成功的 `/edit`（含回覆 task card、固定 ID 編輯與成功的 Retry）會提供 **Undo this edit** 按鈕，也可輸入 `/undo`。只保留最近一次成功修改的還原點；新的修改取代舊還原點，失敗或沒有變更的操作不取代它。不提供完成操作的 Undo，也不提供 Redo。

Undo 還原標題、期限、分類及全部時間欄位；若編輯改了 tag 顯示偏好或關聯作業期限，也一起還原。操作綁定永久 task ID，清單排序不影響目標。任務已完成、刪除，或任務／關聯作業／該 tag 偏好後來被更動時，會拒絕還原；舊 Undo 按鈕不會套用到新修改。Undo、還原點消耗與 Telegram receipt 同一 transaction 保存，重送不會重複還原。還原點會跨重啟保留，`/clear` 確認後一併刪除。

### 期限提醒

所有 open task（含 TronClass 作業）都使用相同提醒流程，不需要 AI：

- 有明確截止時間：到期前 24 小時、1 小時各提醒一次。
- 只有截止日期：前一天與當天 09:00（依 `CHRONOS_TIMEZONE`，預設台北時間）各提醒一次，明確顯示 time unspecified，不推測截止時刻。
- 單純 scheduled work 或 course/event 日期不觸發 deadline 提醒；完成、刪除或移除期限後停止。已過期項目仍出現在每日清單，但不持續發送逾期提醒。

本機 scheduler 每五分鐘檢查一次；Cloud Run 由部署腳本建立獨立的每五分鐘 `/internal/deadlines` Cloud Scheduler job。實際通知可能比門檻晚約五分鐘，另受服務及 Telegram 可用性影響。08:00 每日清單照常執行。服務恢復時只發當下適用的階段，不補發所有錯過階段；日期任務當天 09:00 前不補發前一天提醒。

資料庫以永久 task ID、截止值與階段保存 claim 與送達紀錄；改期會使用新提醒鍵，還原到已通知的期限不重複通知。發送前再次確認任務仍 open 且期限未改。Telegram 與資料庫無法共用 transaction，因此恰好在最後確認後的並行修改仍可能遇到一則已送出的提醒。

Telegram 明確拒絕時可於下一輪重試；timeout、5xx 或發送後尚未記錄就中斷，保留 `sending/uncertain`，不盲目重送，也不宣稱送達。內部端點回覆 503，其他任務仍會繼續檢查。需管理者核對 Telegram 及 `task_action_state` 的 `deadline-*` 紀錄後處理；目前沒有自動解除不明狀態的使用者入口。提醒紀錄不自動到期，`/clear` 確認後清除。

### TronClass 掃描校曆

`/calendar` 查詢每天 06:00（Asia/Taipei）的掃描期間與例外日期；可用 `/calendar disable YYYY-MM-DD`、`enable YYYY-MM-DD`、`reset YYYY-MM-DD`，或 `/calendar term START END` 設定學期掃描區間。讀取不寫資料，更新與 webhook receipt 同一 transaction 保存；重送同一 update 不會重複操作，同時準備的不同指令會依最新設定合併。

另可啟用 [Google task planning](learning-calendar.md)：deadline reminder 的 **Plan tasks** 會要求 Chronos 依任務、截止日期與 Calendar 提出 execution plan，必要時一併列出要挪動的行程。只有按 **Confirm schedule** 才寫入 Google Calendar。預設 work block 為 90 分鐘、可安排於 09:00–21:00，固定承諾與已排 work blocks 每日合計最多 9 小時。`/plan` 查狀態，`/plan <task-id>` 要求 execution plan，`/taskupdate <task-id> <minutes> done|partial|skipped [reason]` 記錄 outcome；`/studyfeedback` 僅保留為相容別名。此功能需要另外完成雲端 Calendar 授權，聊天內的 Google 連線不會自動提供給機器人。

校曆只控制「哪天掃描」，不更換課程 roster 或 TronClass 學期。scanner 目前保留已部署的 `1151` 學期及七門課程範圍；下學期須核對 `TRACKED_SEMESTER` 與 `TRACKED_COURSES` 後另外發布。詳細維運流程見 [TronClass scanner](tronclass-scanner.md)。

### Study 指令範例（需額外設定）

Study 預設關閉，包含 NTOU / TronClass 特定課表與課程 ID；不是跨校通用 LMS 整合。它不下載教材或產生 PDF 摘要，完整邊界見 [Study 課程追蹤](study.md)。

作業通知仍會顯示其永久 task ID，例如 `新作業 #27`；日常操作請用 `/tasks` 取得當下順位，再以 `/edit` 或 `/done` 處理。Chronos 不產生或保存作業草稿；`/prepare`、`/draft`、`/preparation_budget`、`/assignment`、`/exam` 與 `/exams` 均不支援。

`/classday` 接受中文或英文自然語言，用於確認特定課程在某天是否上課：

```text
/classday 10/07 軟體工程不上課
```

`/tasks` 與每日清單使用粗體 `Tasks` 標題。日期格式是每次產生任務卡、清單、編輯摘要與截止提醒都會套用的共用顯示規則；資料庫仍保存完整 ISO 日期與時區。欄位名稱與值之間統一使用空格，不加冒號，例如 `Due 10/20 23:59`、`Course / event 10/16`、`Before None` 與 `After 10/20`；此規則每次產生任務卡、清單、編輯摘要與截止提醒都會套用。時間本身保留 `HH:MM` 格式，任務原文不會被刪除標點。每筆 task 的 title、`MM/DD` 日期（例如 `10/20`）與可選的 `HH:MM` due time 與 tag 各自位於不同文字層級，避免把 metadata 混入標題。過長的多字 tag 只在顯示層縮寫，例如 canonical `computer-architecture` 顯示為 `#CA`；canonical value 仍保留供搜尋與資料關聯。Owner 也能透過 `/edit` 保存自己的 tag display preference。課後回覆原文會保存作為進度證據，並完成原本的進度填寫代辦；回覆本身不會被自動轉成複習或確認範圍代辦。若要建立下一步任務，需要另外發送明確的新增代辦訊息。

設定 `CHRONOS_TELEGRAM_CHAT_ID` 後，其他 chat 無法操作 bot。

Webhook 會驗證 JSON 結構與整數 `update_id`，合法的非文字更新會略過。每筆 message 或 callback 的代辦變更與處理紀錄會一起儲存在 SQLite 或 Firestore；動態位置會在同一 transaction 內解析成永久 ID，重送同一 update 不會重複新增、完成、修改、改期或清除，重啟後仍有效。多訊息回覆會逐則保存 delivery progress，後續 webhook retry 從第一則未確認訊息繼續。若 Telegram 已收到某則訊息而程式尚未記錄送達就中斷，該則文字仍可能重複，但代辦不會重複變更。去重與 pending-edit 紀錄目前不會自動清除；完成或取消 retry 會刪除對應 pending edit。


`/plan` 依狀態分組，已排程的任務另發一則訊息。使用 `/plan ID` 請求 execution plan 或恢復任務；`/planedit ID 明天晚上七點，讀一小時` 可用自然語言調整 execution plan。

部分完成的學習回報會將原任務結案，並建立剩餘工作的接續任務；原回報與任務歷史仍保留。頁數回報會把下一頁至總頁數寫入新任務，沿用期限與專案，並根據本次閱讀速度估算剩餘時間。重複回報不會重複建立任務，Calendar 寫入仍需確認。

回到 [本機啟動](getting-started.md) 或 [README](../README.md)。


Telegram owner menu 可用 `python -m chronos.telegram_menu` 更新並讀回驗證，保留既有項目並加入 `/today`、`/tasks`、`/help`、`/tomorrow`、`/free`。
行程清單會依 execution outcome 顯示 `✓ Finished`、`◐ Incomplete`、`− Skipped`；已結束但尚未回報的 work block 顯示 `◇ Awaiting`。未來行程與一般 Calendar 事件不會被推定為未執行。
`/taskupdate ID minutes done|partial|skipped` 可直接回報已結束的 work block，不必等待背景追蹤。仍在 Scheduled 時會先查證 Calendar 最新結束時間與場次歸屬；尚未結束、已取消或已變更的場次不會寫入回報。`Partially completed` 與 `Skipped` 仍是 check-in 的固定選項。

### Free a calendar window with review

`/replan 清空今天現在到下午兩點的行程` previews moves for activities overlapping
that window. Complete same-day requests with an explicit morning/afternoon/evening
cutoff are parsed directly without AI; past cutoffs are rejected without shifting
to another day. `清空今天下午的行程` deterministically means 13:00–18:00.
Other phrasing uses the validated AI parser. Each activity shows
its original and proposed date/time. **Allow**
applies the reviewed arrangement; **Edit** opens a two-hour text session for a
new window or earliest replacement time (for example `改排到明天以後`); **Cancel**
keeps the original arrangement. `/cancel` also exits this edit session. Every
replacement proposal requires a fresh Allow; old cards cannot approve it.

Only owner-created, non-recurring private activities on the configured
calendar are movable, at most five per proposal. Personal activities must start
in the future. A verified Chronos study booking may already have started, but
must not have ended; its preview states that the full original duration is
retained without inferring completed work. Classes, shared/all-day events,
secondary calendars, ongoing personal events and unverified study bookings stay
in place and are named with their blocking reason. Other eligible activities can
still be reviewed and moved; if none are eligible, no proposal is created. Moves retain event IDs and durations;
tracked study retains its deadline and updates its booking/budget records.
Destinations are searched after the freed window within seven days, respecting
study hours, meals, course policy, reservations and daily study limits. Other
natural-language constraints are rejected rather than silently ignored.

Proposals expire after two hours. Allow rechecks event versions, task snapshots,
calendar conflicts and daily budgets. Calendar writes are sequential, not an
atomic multi-event transaction: if interrupted, some moves may already exist.
Use Allow again to reconcile marked moves without applying a second edit.
Externally changed activities require Calendar inspection; no automatic rollback
is attempted. Nothing is deleted by `/replan`.

## One-time reminders

Send `/remind 今天下午五點半和 Kai 通電話`, or a natural message containing
`提醒`, `通電話`, `打電話`, or `meeting`. Ambiguous dates or AM/PM require a new,
clearer message; no reminder is created until the time is explicit.

Calls and meetings default to 30 minutes. An explicit duration overrides this
value; other reminders occupy a one-minute interval for overlap detection.
Chronos checks the connected Calendar busy intervals and active reminders.
Calendar failures prevent creation. A conflict creates only a pending preview:
choose **Add anyway** or **Cancel**. To change the time, cancel the preview and
send a new request. Approval expires after 30 minutes or at the reminder's start.

`/reminders` lists reminders and their IDs; `/cancelreminder <id>` cancels an
active reminder or pending preview. Reminders do not create tasks or Calendar
events, require no completion update, and leave the active list after delivery.
A confirmed overlap does not reschedule other activities.

The local scheduler checks every minute. Cloud delivery uses the existing
`/internal/learning`, `/internal/study`, and `/internal/deadlines` ticks, so its
latency follows the configured Scheduler cadence (the base deployment's deadline
job runs every five minutes). Notifications can arrive late after downtime.
Delivery claims prevent concurrent or repeated ticks from sending twice. An
ambiguous Telegram outcome remains `uncertain` (or `sending` after an interrupted
process) and is visible in `/reminders`; it is not automatically resent because
Telegram cannot guarantee exactly-once delivery.

Validation covers SQLite and a Firestore transaction fake with mocked Calendar
and Telegram transports; this does not establish a live deployment or delivery.

`/today` shows each linked task’s fixed ID after its duration, for example
`180 minutes(123)`. Calendar events without a task reference omit the ID.


## Reminder confirmation expiry

`/reminders` hides pending overlap confirmations after 30 minutes or once the reminder start time is reached, whichever comes first. Active reminders remain visible. Listing reminders does not change stored state or call AI or Calendar. An expired confirmation cannot activate a reminder; send a new reminder instead.

Interrupted reminder sends become `uncertain` after five minutes. Legacy sending records without timestamps also become uncertain. They remain visible in `/reminders` and are never automatically resent.

Permanent Telegram request failures are marked `failed` and remain visible in `/reminders`. Rate limits wait at least 60 seconds or Telegram retry_after, whichever is greater. Authenticated `/internal/status` includes aggregate reminder counts and attention_required without reminder content.
