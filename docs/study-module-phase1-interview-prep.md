# Chronos Phase 1 面試準備：從課後提醒理解可靠後端

適合讀者：資工系大三；知道函式、HTTP 與基本 SQL，但不必先懂分散式系統。
依據：2026-10-02 repository 實作及驗證紀錄。這是一份可口述、可實作的複習文件。

## 先建立直覺：為什麼提醒系統值得拿來面試？

「下課後傳一句訊息」很簡單。困難在於：程式可能重啟、同一請求可能重送、使用者可能剛好回覆、外部服務可能已收訊息卻沒回應。你需要保護的不是某一行程式，而是整個流程的結果。

Phase 1 追蹤七門課，在表定下課五分鐘後發問。使用者透過 Telegram Reply 回報進度；未回答最多追加兩次提醒，隔日標記 missed。課務狀態以 Firestore 保存，SQLite 支援本機運行。Firestore 是受管理的文件資料庫；SQLite 是嵌入程式程序使用的關聯式資料庫。

學這個專案的實際價值，是能解釋「為什麼一次 API timeout 不等於沒執行」「為什麼資料庫交易不能保證外部訊息只送一次」。相同問題也出現在付款、寄信、排程工作與訂單系統。

### 證據與資訊缺口先說清楚

- 已有 159 個 repository tests 通過的紀錄；這是整個 repository 的測試數，不能說是 159 個 Phase 1 tests。
- 實際 Firestore 測試通過：重複建立、進度與收件紀錄交易、新實例讀回、發送權去重。使用隔離集合，三筆合成文件已清理。
- 實際 Chronos Telegram 出站訊息與 Reply reference 送達；這個 Reply reference 是 bot 發送引用原訊息的第二則訊息。
- 真人回覆經公開 production webhook 的完整流程未驗證；正式 recurring schedule 尚未啟用。
- 未做效能 benchmark、長期 uptime 量測、Firestore 真實高併發 contention 測試；不能宣稱吞吐量或 exactly-once delivery。
- 課程素材下載、摘要與 Markdown export 屬於 Phase 2；假日規則屬於 Phase 4，不是本階段成果。

## 系統流程與必要術語

Webhook 是外部服務在事件發生時，主動呼叫我們 HTTP endpoint 的通知方式。Telegram 發來的 update 是一次事件，update_id 是它的識別碼。

Idempotency（冪等性）是對同一操作重複執行，仍維持預定的同一業務結果；不表示底層每一行程式只執行一次。Deduplication key 是用來辨識「這是否同一個業務操作」的穩定識別碼。

Transaction（交易）把多個資料庫變更綁為一起成功或一起失敗。Atomicity（原子性）描述這種不可只完成一半的性質。Concurrency（並行）指多個執行者的工作時間重疊；即使只有一位使用者，重送與多實例也會產生並行。

```text
每分鐘 tick → 以 Asia/Taipei 判斷課程是否到期
  → 資料庫取得獨佔發送權 → Telegram sendMessage → 保存送達結果
  → 建立 session，保存原始 prompt_message_id

使用者 Reply → 驗證 webhook 與 owner chat → 查原始 prompt ID
  → 同一交易保存進度 + update receipt → 傳送確認訊息

後续 tick → 未回答才提醒 → 最多兩次 → 隔日 missed
```

Session 在此指「某課某日的進度追蹤紀錄」，不是 Phase 0 的 browser login session。Delivery ledger 是保存發送權、嘗試次數與送達結果的紀錄。

### 兩個不同的狀態機

State machine（狀態機）用有限狀態與允許的轉移描述流程，讓不合法變更容易被辨識。

| 進度狀態 | 意義 |
|---|---|
| pending | 已建立課後問題，尚未回答 |
| reminded_once / reminded_twice | 已追加一／兩次提醒 |
| answered | 已保存進度，停止後續提醒 |
| missed | 當日已結束而未回答 |

| 發送狀態 | 意義 |
|---|---|
| sending | 已取得發送權，可能正在呼叫 Telegram |
| sent | 收到有效 message ID 並保存結果 |
| retry | 明確拒絕後允許有限次重試 |
| failed | 明確拒絕且已用完重試次數 |
| uncertain | 無法確定外部是否已送達，不自動重送 |

分開建模是因為「使用者是否回答」和「一次網路發送是否成功」是兩個問題。發送失敗不代表使用者缺席，回答成功也不能取消已在網路中的訊息。

## 面試題庫

每題先讀口述回答，再追到程式與測試。不要一次把全部細節塞進第一個回答；先說結論、機制與限制。

### 一、核心概念

#### 1. 你在這個專案如何實現 idempotency？

**評估能力：** 能否選對操作的身分，並區分資料變更與外部副作用。

**口述回答：**「我用了不同層級的 key。課程加日期辨識同一堂課的 session，Telegram update_id 辨識重送事件，session 加 prompt 或 reminder 編號辨識一次通知。進度更新與 update receipt 在同一交易中保存，所以重送同一 update 不會重複更改進度。但這不代表 Telegram 訊息有 exactly-once 保證，外部送達結果不明仍要另外處理。」

**原理：** 唯一識別配合原子性持久化，避免 check-then-act race；後者是檢查與執行之間，另一個執行者先改變狀態的競爭條件。

**追問與方向：** 同文不同 update_id 呢？它們是不同事件，還需要 answered 狀態防止覆寫。只用時間戳當 key 呢？重送時間不同，會破壞同一操作的穩定身分。

**誤解：**「有 if exists 就安全」忽略並行；「冪等就是只送一次」混淆資料庫結果與網路送達。

#### 2. 為什麼 timeout 不能直接重試？

**評估能力：** 對網路失敗與未知結果的推理。

**口述回答：**「timeout 只表示我沒拿到回應，Telegram 可能已經送出了訊息。立刻重試會讓使用者收到重複提醒。我把它存成 uncertain 並停止自動重送；明確被拒絕的情況才有限重試。這個策略偏向避免重複打擾，代價是某些未送達訊息需要人工確認。」

**原理：** 呼叫端觀察與遠端執行不在同一時間點；網路無回應不能推出遠端未執行。Trade-off 是工程選擇的利益與代價。

**追問與方向：** 怎樣做到真正去重？若 provider 支援 idempotency key，可以把同一 key 傳給對方；本專案 Telegram sendMessage 沒用到這類保障。uncertain 是否等於 failed？不是，前者不知道結果，後者有明確拒絕證據。

**誤解：**「timeout 就是失敗」「多試幾次就一定更可靠」。

### 二、實作細節

#### 3. 如何把 Reply 綁定正確課程與日期？

**評估能力：** 資料關聯、輸入驗證與避免自然語言歧義。

**口述回答：**「發問時保存 Telegram message ID，並把它連到課程加日期的 session。Webhook 只用 reply_to_message.message_id 查對應 session，再保存文字進度。一般訊息不靠文字猜課程，未知 Reply 也不會落入 AI 建立待辦流程。這讓『第四章』這種簡短回答仍有明確上下文。」

**原理：** Correlation 是把事件連回原始操作的關聯；應使用結構化 ID，而不是容易歧義的內容。

**追問與方向：** 不同 chat 的 message ID 相同？目前是 owner-only chat，guard 先限定來源；多使用者版需要 chat ID、prompt ID 與 owner 一起建索引。回覆提醒訊息可以嗎？目前要求回覆原始 prompt，提醒文字明示此限制；不能宣稱所有 thread reply 都支援。

**誤解：** 用課名 substring 或「今天最近一堂課」推測，遇到晚回覆就配錯。

#### 4. 每分鐘 tick 如何避免多送提醒？

**評估能力：** 持久化排程、時間邊界與恢復流程。

**口述回答：**「每分鐘重新從持久狀態判斷到期工作，而不是靠程序記住已執行到哪裡。每個 prompt 與 reminder 都有固定 key，先用交易 claim 發送權，再呼叫 Telegram。sent receipt 可用來補建 session；answered 或 missed 不再提醒。第二次提醒從第一次實際紀錄的送出時間加一小時，因此停機恢復不會連發兩次補發。」

**原理：** Durable state 是程序重啟後仍存在的狀態；tick 是反覆重新評估到期工作，而不是一次性記憶體 timer。

**追問與方向：** 程式停兩天呢？僅對當日課程補發，過期 session 清理為 missed。sent 後未建立 session 就當機？下次從 receipt 補建，不重新發送。

**誤解：** 用一個 global boolean 記錄「送過了」；把單機 timer 當成 Cloud Run 上的持久排程。

### 三、設計決策

#### 5. 為什麼不把 Telegram 呼叫放在 Firestore transaction？

**評估能力：** 交易重試、外部副作用與責任邊界。

**口述回答：**「Firestore 可能重跑 transaction callback 來處理衝突，所以 callback 必須只計算資料狀態。如果裡面傳 Telegram，callback 重跑就可能多送訊息。我先用交易記下發送權，再在交易外送出，最後記錄結果。這保護了資料一致性，但也承認送出與存檔之間存在中斷空隙。」

**原理：** Transaction callback 是可重播的計算；side effect 是外部可觀察的改變，不能隨意重播。資料庫交易不涵蓋 Telegram。

**追問與方向：** 這是完整 transactional outbox 嗎？有持久發送意圖的元素，但目前不是包含可安全重播外部發送的完整 outbox 系統。claim 兩分鐘過期後重發嗎？不重發，轉 uncertain，避免慢 worker 還在送。

**誤解：**「包 transaction 就會把 API 一起 rollback」。

#### 6. 為什麼 SQLite 與 Firestore 都保留？

**評估能力：** 測試替身與真實 backend 語意差異。

**口述回答：**「SQLite 讓本機測試便宜、快速且可重現，Firestore 則承擔 Cloud Run 的持久 state。兩者暴露相近操作，卻不能假設底層交易語意完全相同。因此我有 SQLite 並行 claim 測試、Firestore fake lifecycle 測試，也做了隔離的真實 Firestore 寫入與重新讀回。」

**原理：** Repository 是把持久化細節封裝成業務操作的邊界；相同介面不自動保證相同一致性行為。

**追問與方向：** fake 能證明什麼？流程與契約，不是網路、IAM 權限或真實 contention。為何 Firestore reads 要先於 writes？該 SDK transaction 使用限制要求如此，callback 要安排讀取後再變更。

**誤解：**「SQLite 通過等於雲端已驗證」或「寫兩份相同程式就是 backend independence」。

### 四、除錯情境

#### 7. 使用者收到重複提醒，你會怎麼查？

**評估能力：** 以證據區分不同 failure boundary（失敗邊界）。

**口述回答：**「先確認是同一 delivery key 的重送，還是兩個不同 session。再看 claim、attempt_count、sent receipt 與 message ID 是否一致，檢查是否有兩條排程或舊 prototype 同時在送。最後重現送出後存檔前中斷的情境。不能只加一個 sleep，因為那沒有消除競爭。」

**原理：** 先識別事件，再看狀態轉移與副作用；有機制的故障注入比猜測有效。

**追問與方向：** 八個並行請求只一個 claim 的測試，能代表所有部署嗎？只證明該 SQLite 測試條件，不能外推真實 Firestore 高併發。舊 service 怎麼辨識？實際路徑是 main → study_scheduler；course_tracking_service 是歷史 prototype。

**誤解：** 直接減少 worker 數或開更長 timeout，卻不查 key 與交易。

#### 8. 已回答，為什麼還收到一則提醒？

**評估能力：** 能否區分 in-flight message 與 lost update。

**口述回答：**「提醒可能在回覆前已開始呼叫 Telegram，因此無法收回。我能保證提醒完成時重新讀取最新 session，不把 answered 覆寫成 reminded。若回覆後又持續收到新的提醒，那才需要查 terminal-state guard 或關聯錯誤。兩種現象不能混為一談。」

**原理：** Lost update 是舊資料覆蓋新變更；atomic read-modify-write 可以保護 state，但不會撤回已發生的外部請求。

**追問與方向：** 要完全杜絕任何晚到訊息？需要更強協調且仍受外部配送時間影響，要先定義可承諾的規格。怎麼測？fake messenger 在 send 中觸發回覆，assert 最終仍 answered 且下一輪不再提醒。

**誤解：**「加鎖後外部已發出去的請求也能取消」。

### 五、效能與安全

#### 9. 七門課的系統需要如何考慮效能？

**評估能力：** 先量測、再優化；辨識 blocking I/O 與成長成本。

**口述回答：**「目前資料量小，所以優先保護語意與持久性，還沒有吞吐 benchmark。每分鐘查 pending sessions 與 failed deliveries 有讀取成本；SQLite 目前的 failure listing 會掃紀錄，資料長大後要加索引或保留政策。另外同步 Firestore 呼叫位於 async 流程，可能阻塞 event loop，多使用者時要量測並改用 async client 或受控執行緒。」

**原理：** Event loop 是協調 async 任務的執行迴圈；阻塞 I/O 仍會阻塞它，async def 本身不會讓同步操作非阻塞。

**追問與方向：** 量什麼？tick duration、DB reads、pending 數量、發送延遲、重試率；目前沒有結果不能編數字。為何現在不立即引入 queue？queue 增加部署與運維成本，先根據負載和隔離需求決定。

**誤解：**「用了 async 所以一定高效能」「159 tests 就證明可以大規模運作」。

#### 10. 如何避免 bot token 或學校 secrets 泄漏？

**評估能力：** 是否理解 secrets 的完整 data flow。

**口述回答：**「Telegram token 位於 API URL path，所以即使不主動 print token，HTTP client 的 INFO log 也可能泄漏。程式關閉相關傳輸 logger，live probe 只輸出布林結果與 exception class，不輸出 raw response 或 URL。課後追蹤本身不需要學校帳密、cookie 或 CSRF token，因此不應把它們加入資料模型。」

**原理：** Data minimization 是只持有完成任務所需的資料；secret safety 必須涵蓋 logging、exceptions、Git、資料庫及通知。

**追問與方向：** 關 logging 的代價？減少診斷資訊，所以提供安全的 operation/status/counter。owner-only 夠嗎？仍需要 webhook secret、chat guard 與嚴格輸入；目前設定與啟用前需核對，不能宣稱任何缺省配置都安全。

**誤解：**「放 .env 就完全安全」「private repo 可以 commit token」。

### 六、延伸思考

#### 11. 多使用者版本要改哪些設計？

**評估能力：** 能否辨識目前假設與擴充時的不變條件。

**口述回答：**「目前固定 owner，所以 session key 主要是課程與日期。多使用者要把 owner 身分納入 session、delivery key 和查詢條件，Reply correlation 也要包含 chat ID。課表、時區、權限與排程公平性都要 per-user 管理，不能只拿掉 owner guard 就上線。」

**原理：** Tenant isolation 是不同使用者或組織之間的資料和權限隔離；穩定 ID 必須涵蓋真正的唯一性範圍。

**追問與方向：** 共用一門課如何節省？教材 metadata 可有共享層，但進度、訊息與授權仍分離。是否要改 database？先評估 access pattern、indexes 和吞吐，不能只因多人就換技術。

**誤解：** 全域 message_id 唯一、所有使用者可以共用同一進度紀錄。

#### 12. 你怎麼知道這階段完成了？

**評估能力：** 工程誠信、驗收範圍與測試分層。

**口述回答：**「我按 PRD 逐項找證據：七門課發問時間有 parameterized tests，回覆去重有 webhook tests，提醒與跨日有 scheduler tests，Firestore 有隔離 live check，Telegram 有實際出站訊息。159 是整個 suite 的結果。我只宣稱 Phase 1 實作完成；正式 recurring schedule 未開，真人 production webhook 和部署仍需後續驗證。」

**原理：** Evidence scope 必須與 claim scope 一致；測試能支持的是實際覆蓋的條件，不是所有想像中的行為。

**追問與方向：** 有何剩餘風險？外部配送不確定、多實例真實 contention、同步 I/O 及部署配置。若下一步只能做一件事？在受控環境完成真人 Reply → webhook → Firestore 的端到端證據。

**誤解：** 把 health endpoint 成功當所有服務成功，或把 bot 自己送 Reply reference 說成真人 webhook 已驗證。

## 必須掌握的知識與用途

以下每列是一個學習主題；「用途」分別對應面試、專案、未來工作。先理解問題，再練習寫出能證明行為的測試。

| 分級／主題 | 為什麼學、解決什麼 | 讓你更精確理解的決策 | 面試／專案／工作用途 |
|---|---|---|---|
| Must know：idempotency 與 stable identity | 重送很常見，避免同一事件重複改資料 | update key、session key、delivery key 不能混用 | 解釋重試／防重複功能／訂單與付款 API |
| Must know：transaction 與 race condition | 保護進度與 receipt 不只存一半 | check 與 write 必須原子執行；外部 API 不屬於交易 | 分析 lost update／寫可靠 repository／並行事件處理 |
| Must know：state machine 與 uncertainty | 避免把未知結果硬算成功或失敗 | answered 和 delivery sent 是不同維度 | 解釋 timeout／設計狀態／處理 incident |
| Must know：timezone 與 elapsed time | 午夜與重啟會改變到期判斷 | 本地日期界線與實際 send 時間分開計算 | 時間題／課表與提醒／跨區服務 |
| Should implement：webhook correlation | 短句本身沒有課程上下文 | 用 prompt ID 配 session，先驗 owner | API 設計／聊天互動／事件整合 |
| Should implement：durable claim 與 recovery | 程式中斷後仍需知道是否能再送 | claim 需持久化，uncertain 不直接重新搶送 | 系統設計／排程可靠性／通知 pipeline |
| Should implement：failure injection tests | 正常路徑測不出中断與衝突 | 控制時鐘、在 send 中觸發 reply、重建 repository | 測試說明／重現 bug／事故防再發 |
| Should implement：secret-safe diagnostics | token 能藏在 URL 與例外裡 | 用 allow-listed fields 代替完整 payload | 安全面試／觀測性／維護 production |
| Worth exploring：transactional outbox 與 provider 去重 | 跨資料庫與外部系統不易原子完成 | 可持久意圖不等於 exactly-once effect | 分散式設計／背景任務／金融與訊息系統 |
| Worth exploring：async I/O、索引與讀取成本 | 規模成長後每分鐘扫描會變貴 | 依 access pattern 和量測決定優化 | 效能題／容量規劃／成本控制 |
| Worth exploring：多租戶隔離與公平排程 | 多使用者後碰撞與權限問題增加 | identity scope、per-user limits、時區配置 | 擴展設計／SaaS／平台工程 |

## 可自然使用的 English expressions

- “The transaction protects database state, not the external side effect.” 說明原子性邊界。
- “A timeout leaves the delivery outcome unknown.” 避免把 timeout 說成一定未送達。
- “We persist intent before performing the network call.” 描述發送前持久化意圖。
- “I would reproduce the failure window before changing the retry policy.” failure window 指兩步之間可能中斷的時間區段。
- “The tests cover the local behavior; production activation is a separate check.” 清楚交代證據範圍。
- 常見搭配：claim exclusive ownership、persist a receipt、correlate a reply、bound the retries、preserve terminal state、reconcile after a restart、inject a failure、verify the assumption。

## 一頁式重點摘要

**專案一句話：** Chronos 依七門課課表，在下課五分鐘後透過 Telegram 收集进度；最多提醒兩次，Firestore 保存流程狀態。

**最重要的三個 ID：** course + date 識別 session；update_id 識別 webhook event；session + notification kind 識別一次發送。

**最重要的交易：** 進度與 update receipt 一起提交；送訊息在交易外。Firestore callback 可能重跑，不要放 network side effect。

**失敗策略：** 明確拒絕最多三次、間隔五分鐘；timeout 或 malformed response 為 uncertain，不自動重送。兩分鐘未完成 claim 轉 uncertain。

**時間規則：** Asia/Taipei；首次表定 end + 5 minutes；提醒相隔一小時；隔日不補舊課提醒；晚到回覆在 receipt transaction 判過期。

**並行規則：** reminder completion 重新讀最新狀態，不能蓋掉 answered。已 in flight 的訊息可能仍到達。

**實證：** 159 repository tests；SQLite 八個競爭 claim 只有一個成功；隔離 live Firestore 通過；兩則真實 Telegram 訊息驗證出站與引用關係。

**不可宣稱：** production recurring schedule 已啟用、真人 webhook 已跑通、exactly-once external delivery、高吞吐量或長期穩定性。

**60 秒口述：**「我做的是個人課務追蹤，但核心問題是可靠事件處理。我用 course/date 建 session，以 Telegram 原始 message ID 綁定進度，並把進度與 webhook receipt 放在同一交易。發送則先取得持久 claim，因為 API timeout 不能證明沒送出，所以 outcome unknown 不自動重送。最後用時鐘邊界、重啟和並行回覆測試驗證規則，並分開報告本機測試、真實 Firestore、Telegram 出站與尚未做的 production webhook 驗證。」

## 建議學習順序

1. 先看 `course_tracking.py`，手畫進度狀態機；能解釋 session 與 login session 的差別。
2. 看 `main.py` 的 Telegram model 和 reply branch；追 prompt ID 如何找到課程。
3. 看 `db.py`、`firestore_db.py` 的 process_update 與 record_course_reply；畫交易讀寫順序。
4. 看 `study_delivery.py`；列出每個中斷點會留下什麼狀態。
5. 看 `study_scheduler.py`；用固定時鐘手算兩次提醒和隔日清理。
6. 讀 tests 後加入 failure injection；最後看 live evidence，練習只講證據支持的結果。

## 實作練習：做出結果，不只背答案

### 練習 A：交易重送

建立一個合成 session，對同一 update_id 提交兩次不同進度。預期第二次回原 receipt，進度維持第一次內容。接著刻意把 receipt 保存移到另一筆交易，注入中斷，解釋為何保障變弱。僅在本機測試分支實驗。

### 練習 B：崩潰時間線

分別模擬 claim 前、claim 後 send 前、send 成功後 receipt 前、receipt 後 session 前中斷。每一格寫下「DB 知道什麼」「外部可能做了什麼」「重啟可以做什麼」。預期最後一格可以補建 session；中間不明格不能盲目重發。

### 練習 C：午夜與延遲提醒

將時鐘设為台北 23:59、隔日 00:00、停機兩天後。驗證不跨日提醒、舊 session 會 missed。把第一次提醒延遲四小時，驗證第二次不在下一分鐘補發。

### 練習 D：競爭與回覆

用八個並行 worker 搶同一 key，assert 一個 claim。再讓 fake messenger 在 send 途中提交回答，assert 完成後仍 answered。說明這個測試不能證明外部訊息被撤回。

### 練習 E：backend contract

用相同情境跑 SQLite 和 Firestore fake：建立、重複建立、回覆、重送、過期、重新讀取。列出 fake 未覆蓋的 IAM、網路與 transaction contention，再設計隔離 live test；不要把真實帳密寫進 fixture。

### 練習 F：安全觀測

讓 fake HTTP client 用合成 token URL 報錯，assert 捕獲的 log、API error、通知不包含該字串。再要求輸出 operation、safe status、attempt count；體會隱私與可除錯性可以一起設計。

## 最容易只背答案的觀念

| 背誦式說法 | 真正要能解釋的因果 |
|---|---|
| transaction 就不重複 | 交易只保護其涵蓋的資料，外部 API 不在內 |
| 有 UUID 就冪等 | UUID claim 用於一次持有者，穩定 delivery key 才辨識同一通知 |
| retry 提升可靠性 | 必須先知道操作是否可重播以及第一次可能已完成 |
| 每分鐘 tick 就準時 | 程序停止、排程延遲、網路與時鐘仍會影響到達時間 |
| async def 不阻塞 | 同步 SDK 操作仍可能阻塞 event loop |
| fake 綠燈等於整合成功 | 真實權限、索引、網路與 service behavior 需要各自證據 |
| 私人 bot 沒安全風險 | URL token、log、webhook guard 仍是攻擊或泄漏邊界 |

## 原始依據與可重現驗證

原始範圍：`docs/study-module-prd.md` 的 Phase 1 與課後進度流程。
驗收：`docs/study-module-phase1-acceptance.md`。
實測：`docs/study-module-phase1-live-evidence.md`。
操作：`docs/study-module-phase1-operations.md`。

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-phase1-current
```

重點測試：`test_study_scheduler.py`、`test_study_delivery.py`、`test_system.py`、
`test_firestore.py`、`test_db_course_sessions.py`、`test_study_failure_notices.py`。
live probe 會寫入合成資料或發送訊息，不應視為沒有副作用的 unit test。
