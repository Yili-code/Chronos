# 使用 Chronos

直接說你想做什麼，不必記住功能指令。

| 需求 | 例句 |
| --- | --- |
| 新增任務 | 明天五點前交報告 |
| 修改任務 | 報告改成星期五交 |
| 優化用詞與標籤 | 優化任務用詞，並根據課程建議標籤 |
| 完成任務 | 資料庫報告做完了 |
| 撤銷修改 | 撤銷剛剛的修改 |
| 查行程 | 我明天下午有什麼安排？ |
| 安排工作 | 幫我安排資料庫報告的時間 |
| 空出時段 | 幫我空出今晚 |
| 拆解目標 | 幫我拆解下週五要交的報告，每天最多90分鐘 |
| 一次性提醒 | 明天下午三點提醒我打電話，不用追蹤完成 |
| 課程與公告 | 明天軟體工程不上課／查看已保存公告 |
| 查詢作業 | TronClass 沒有新作業？／資安課還有作業嗎？ |
| 重新檢查作業 | 現在幫我檢查 TronClass |

回覆任務卡說「延到明天」或「完成了」，就會使用該任務。多個名稱相近的任務會先詢問目標；可回答名稱、固定 ID，或追問清單中的第幾項。追問保留十分鐘，過期後請重新描述。

行程改排、期限拆解與清除所有任務會先供你確認。空出「今晚」以 18:00 至午夜為範圍；若已入夜，從現在開始。預覽會顯示實際時段及不能自動移動的活動。

自然語言新增可一次拆成多筆任務（每批最多 20 筆），每筆有獨立編輯卡。例如「一次新增多筆 tasks：複習 database 第一章到第五章，需附上 hashtag」應拆為五筆，各自使用 `#database`。標籤要求存入 project，不放進標題或內容；沒有指定拆分時，章節範圍可維持一筆。完整批次解析通過後才寫入，解析失敗不新增任何一筆。`/add` 仍是單筆原文新增。

「不需要記錄」「不用更新 task list」或單純確認收到時，只回覆「收到」，不保存為課程進度、不完成代辦，也不附任務清單。課程填寫進度的代辦仍保持原狀；「今天沒上課」和「不知道上到哪裡」仍是有意義的課程回報。

## 記住偏好

可以說「記住我比較喜歡晚上讀書」「以後叫我 YiLi」「我有哪些已保存的偏好」「忘記我的讀書時間偏好」。偏好持久保存於目前使用者的對話範圍，最多 20 項；同一主題可更新，不需要建立待辦。

後續 AI 任務解析與規劃會讀取目前的偏好，作為可行選項之間的軟性建議。當次明確要求、既有產品規則與可用時段優先；例如偏好晚上但只有上午可用，不能擅自排到晚上的忙碌時段。忘記後的下一次請求不再提供該偏好。固定卡片文字、排程設定與既有活動不因保存偏好自動重寫，行程寫入仍須確認。

## 快捷入口

`/task` 任務、`/plan` 計畫、`/today` 今天剩餘行程（現在至午夜，含進行中與全天行程）、`/tomorrow` 明天、`/free` 空檔、`/info` 簡短說明。選單只列不需參數的入口。完成任務可直接說名稱，或手動輸入 `/done 1` 完成目前清單第 1 項。

`/start` 直接顯示目前任務清單，與 `/tasks` 相同；操作說明仍可用 `/info` 查看。

直接回覆新產生的 `/today` 清單，可用完整任務名稱或「第一個」等順位完成、調整已連結的任務。順位依被回覆的那則行程訊息，與 `/done 1` 的全域任務順位不同。缺少目標或時間會追問；跨日或任務已變更時需重新 `/today`。目前一次操作一項，未連結任務的 Calendar 活動需到 Calendar 修改或取消。舊訊息不會補建回覆綁定。

原有 `/tasks`、`/edit`、`/undo` 等指令仍可使用，但不再列入選單。AI 無法理解或暫時不可用時，不會把訊息當成新任務；快捷查詢與明確指令仍可使用。

## TronClass 作業查詢與手動檢查

「TronClass 沒有新作業？」只讀取已保存資料，顯示上次成功檢查時間、上次掃描新增數與已追蹤且未結案的作業。資料超過一小時、從未成功檢查或最近失敗時會明確標示；沒有新增不代表原有作業已完成，也不會把舊資料說成即時結果。可用課程名稱查詢，或 `/assignments [課程代號] [頁碼]` 每頁查看五項，例如 `/assignments security`、`/assignments database-systems 2`。

「現在幫我檢查 TronClass」、`/refreshassignments` 或查詢卡的 **立即檢查並同步新作業**，會先確認收到要求，再啟動既有雲端掃描器，完成後回覆本次結果；查到的新作業沿用原流程加入任務及發送首次發現卡。手動掃描涵蓋已設定的全部課程；課程限定要求先顯示該課程資料及全範圍檢查按鈕，不默默擴大掃描。重複要求會合併，同一已完成要求五分鐘內不再次啟動；進行中要求保留三十分鐘。啟動結果不明時不盲目重試，逾時會標示無法確認結果。

手動檢查失敗會直接回覆這次要求；排程背景掃描仍沿用連續相同失敗才主動警示的規則。掃描日期停用時回覆暫停，不會當作零筆新作業。手動功能需另行啟用，未設定雲端 Job 時仍可查已保存資料。設定與部署順序見 [TronClass scanner](tronclass-scanner.md)。

## 任務用詞與課程標籤

任務清單的 **Review wording & course tags** 或 `/reviewtasks` 可產生既有任務的建議。每次最多檢查前 20 項；預覽顯示原標題、建議標題、可能對應的課程與標籤理由。按 **Apply** 才更新該任務，按 **Keep unchanged** 保留原狀。每張卡片十分鐘內有效，且綁定固定任務 ID、內容快照與原訊息；任務已被修改或完成時不套用舊建議。更新成功或保留原狀的結果訊息會在顯示 7 秒後自動刪除；更新成功卡片上的 Undo 按鈕在這 7 秒內可用。

新任務沒有明確標籤，且主題明確對應目前課程時，另附課程標籤建議卡；確認前保持未分類。批次新增也逐項建議；只說「附上 hashtag」時，推測的課程標籤仍需確認，明確提供 `#os` 等值則直接沿用。推測不覆寫既有標籤。預設短標籤為資安 `#ispm`、計算機結構 `#ca`、軟體工程 `#se`、圖論 `#ga`、資料庫 `#db`、程式競賽課 `#cp`、作業系統 `#os`；已有同課程的標籤或顯示別名時優先沿用。AtCoder 等獨立比賽不因涉及程式設計就自動建議課程標籤。

英文標題依工作內容使用 Study、Review、Watch 或 Complete；不為了讓文字流暢而猜測觀看、複習或完成。章節與 lecture 編號保留，進度細節放在 content。既有標題調整也必須確認。單純查看任務清單不呼叫 AI，也不儲存推測標籤；主動要求建議會把任務標題、既有標籤與 content 傳給已設定的 Gemini，並計入用量。

## 資料與限制

自然語言意圖判斷會把輸入、目前時間、未完成任務的標題與時間欄位、有效追問，以及相關提醒與已保存公告的名稱／識別碼傳給已設定的 Gemini。新增與修改可能再呼叫一次 AI；使用量會列入統計。追問內容保存在資料庫，逾時後不再用作上下文；輸入也可能保存在任務時間來源、預覽或 Telegram 處理紀錄。

撤銷僅適用最近一次成功的任務編輯，不包含完成或清除。排程需要 Calendar 授權；Study、Gmail 需另外啟用。Chronos 不代寫或提交作業。不支援的要求會說明或追問，不自動改做新增任務。

設定見 [本機啟動](getting-started.md)，排程細節見 [Google Calendar](learning-calendar.md) 與 [期限拆解](backward-planning.md)。

維護者可執行 `python -m chronos.telegram_menu`，將 owner 選單替換為上述六項並讀回驗證。程式修改、Git 推送與正式部署是不同步驟。


TronClass 匯入的作業使用可讀的課程英文簡稱：`#DB`（Database Systems）、`#CA`（Computer Architecture）、`#ISPM`（Information Security Practice and Management）、`#OS`（Operating Systems）、`#GA`（Graph Algorithms）、`#SE`（Software Engineering）、`#CP`（Introduction to Competitive Programming）。課程 ID 僅作來源識別；既有數字課程標籤在任務卡與清單顯示時轉成同一簡稱，不改寫既有資料或使用者設定的別名。未登錄的課程不產生數字標籤。新增追蹤課程時必須一併登錄簡稱，測試會檢查課表與映射是否完整對應。

## 課後任務偏好

程式競賽技巧導論預設在課後五分鐘提出「學習本週上課內容」與「完成至少六題程式設計題目」兩項建議。每項可獨立接受或略過；只有接受後才新增待辦，不推定截止時間、不直接建立 Calendar 活動。建議保留七天；重複點擊不會重複新增。停課與考試期間沿用原有課程日政策。

其他課程維持詢問進度。回覆該課原始訊息「通常都是 1. 第一項 2. 第二項」，可明確記住該課兩項偏好，供之後課後建議使用。也可用自然句子、中文編號、條列或英文描述固定習慣；例如「每次下課後都先複習本週內容，再完成至少六題程式題」。可說「以後第二項改成十題，第一項不變」或「以後不要再固定提出課後任務」。一般進度及明確表示不用記住的單次回報不會改寫偏好。

「以後照這樣做」只有在回覆的卡片明確列出兩項建議時才能沿用；缺少上下文就會追問。語意解析使用已設定的 Gemini，輸入與該課已有偏好／原卡片兩項內容會傳給模型；失敗或格式不合格時不修改偏好或進度。一般進度沿用原流程，不新增 AI 呼叫。保存前再次核對偏好，避免覆蓋解析期間的新設定。十項合成案例及限制見 [辨識驗證](audits/course-habit-intents.md)。

使用 `/coursehabit competitive-programming` 查看偏好；`/coursehabit competitive-programming | 複習本週內容 | 完成十題` 修改；`/coursehabit competitive-programming | off` 停用。修改只影響之後建議，已送出的卡片保留原內容。只輸入 `/coursehabit` 可查看所有課程代碼。偏好保存在目前資料庫；不跨獨立部署共用。


新增指定時段的考試會建立 Calendar 確認卡片，分類為 `exam`；準備或複習考試仍是待辦，競賽則使用 `contest`。卡片保留英文標題、日期、起訖時間、時長、分類與額外備註，不重複附上原始輸入。確認後，Calendar 描述仍保留原始輸入供追溯；按下 Allow 前不會寫入日曆。


活動成功新增且確認訊息送達後，會刪除確認卡片與使用者原始請求，保留新增成功的訊息。取消、衝突、寫入或確認訊息投遞失敗時，原始請求會保留。舊卡片未記錄原始訊息 ID，無法安全追溯刪除。考試使用紫色，與既有考試配色一致；不自動改寫既有日曆事件的顏色。


### 計畫概覽

`/plan` 顯示待安排、已延後與已安排的數量，最多提供兩個待安排任務按鈕；按鈕順序沿用既有清單，不代表優先順序。點選任務只請求提案，仍須確認後才寫入行事曆。

「View all plans」展開所有計畫狀態，每頁最多六件；「Scheduled tasks」只看已安排任務。導覽只讀取資料，不會生成提案或改動排程。完整指令仍可從 `/help` 查閱。

### Task list scheduling groups

The task list groups open tasks into **Scheduled** and **Unscheduled**. Confirmed Calendar bookings show their local start and end times alongside any deadline. Pending proposals are not confirmed bookings. Existing planned times also appear in Scheduled. Empty groups are hidden. Numbers retain their original command positions across groups, so `/done N` and `/edit N` still select the displayed task.


### Conversational replies

Greetings, thanks, identity questions and explicit no-change acknowledgements receive concise replies without an AI request or task mutation. Chronos identifies itself as an AI assistant when asked. Whole-message matching keeps mixed requests such as “Thanks, remind me tomorrow” in normal intent routing. Social turns preserve pending task clarification; explicit creation still allows a greeting as a task title.


### Follow up on a scheduled work session

After a verified work block ends, Chronos asks what happened. No reply means the outcome is unknown, not that the task was skipped or completed. One additional check-in is allowed after 24 hours during working hours; uncertain delivery is not automatically retried.

Choose **Did not start** to record zero work. Chronos asks what prevented the start, shows the unchanged deadline and repeated non-start count, then offers another time, a shorter session when possible, deferral, or stopping further planning. A new work session uses the existing Calendar conflict, deadline and capacity checks. Routine sessions are scheduled automatically by default; exceptions require a decision. Shorter means a shorter first session, not a claim that the entire task needs less work.

Stopping planning requires explicit confirmation and retains the open task, deadline and past Calendar event. Deferral also retains the task. Neither means completed. Use `/plan` → **View all plans** → **Review next step** to resume an unresolved decision, or `/plan ID` to explicitly request a fresh proposal.


### Automatic planning

Routine task work sessions now use default acceptance: Chronos checks current task data, availability, deadline, working hours and daily workload, books the session, then sends **Scheduled** with an **Adjust time** button. There is no routine proposal card or Accept step. Nightly planning and recovery after a reported non-start use the same booking path. Scheduled-task details also provide **Adjust time**.

A proposal remains necessary when arranging the session would move another event or exceed normal working hours. No available slot produces an explanation and adjustment options. Deadlines are not changed automatically. Planning can be restored to explicit approval with `CHRONOS_LEARNING_AUTO_ACCEPT=false`; automatic planning is the default while the existing learning-calendar activation/authorization requirements remain.

If Calendar creation succeeds but its response is lost, Chronos reconciles the deterministic event before notifying. A rejected notification can be retried; uncertain notification delivery is shown for review instead of automatically resending or creating another booking.


### Natural-language changes to booked work sessions

Name the task and ask for an exact time, a relative shift, a changed duration, or an available morning/afternoon window. Today-only earliest availability stays within today. Explicit starts such as 14:10 are preserved rather than rounded to quarter-hour boundaries. Ambiguous targets/times ask for clarification; occupied or policy-incompatible times leave the original event unchanged. See the [ten-case synthetic audit](audits/natural-schedule-edits-10.md) for examples and verification limits.
