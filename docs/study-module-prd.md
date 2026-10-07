# Chronos Study Module — Product Requirements Document

> **2026-10-05 owner decision — resumed:** YiLi 明確要求完成整份 Study Module PRD，
> 恢復開發，包括逐段 AI 重點生成。Phase 2 尚未驗收完成；重新開發不等於
> 生成可用性或內容品質已通過，也不解除既有資料傳送與免費額度限制。
> 保留 PDF 下載、保存、選檔、既有筆記與測試；不恢復合併摘要功能。
> 詳見 [Phase 2 狀態與重啟條件](study-module-phase2-status.md)。

## 1. Product definition

Chronos Study Module 是整合進既有 Chronos 的個人課務助手。

它根據課表與海大校曆主動追蹤上課進度，透過 Telegram 收集 YiLi 的課堂進度，從 TronClass 取得教材、公告與作業，再以 Gemini 產生講義摘要及作業準備內容。

**Target user:** YiLi  
**Deployment:** Existing Chronos on Google Cloud Run  
**Interface:** Existing owner-only Telegram bot  
**Timezone:** Asia/Taipei  
**Cost target:** Zero incremental monthly cost，超出免費額度時 fail closed，不切換至付費模型。

---

## 2. Tracked courses

| Day | Time | Course | Progress prompt |
|---|---|---|---|
| Monday | 09:20–12:05 | 資訊安全實務與管理 | 12:10 |
| Tuesday | 13:10–16:00 | 計算機結構 | 16:05 |
| Wednesday | 09:20–12:05 | 軟體工程 | 12:10 |
| Wednesday | 13:10–16:00 | 圖論演算法 | 16:05 |
| Thursday | 09:20–12:05 | 資料庫系統 | 12:10 |
| Thursday | 18:30–21:10 | 程式競賽技巧導論 | 21:15 |
| Friday | 09:20–12:05 | 作業系統 | 12:10 |

Excluded:

- 微積分
- 星期六三小時課程
- 其他未列入課表的課程

---

## 3. Post-class progress flow

### 3.1 Initial prompt

每堂課表定下課五分鐘後，Chronos 發送：

> **軟體工程** 今天的進度

接受的回答包括：

- 「第四章」
- 「到第 48 頁」
- 「ch04_UML」
- 「講完 sequence diagram，開始介紹 design pattern」

YiLi 必須使用 Telegram 的 **Reply** 回覆該課程訊息，避免系統把一般對話誤判成課程進度。

### 3.2 Reminder policy

2026-10-05 YiLi 選擇「調查與複習兩種代辦」：新課後通知確認送達後，
在同一資料庫交易建立 session 與「填寫課程進度（日期）」代辦。
使用 Reply 回覆有效進度時，在同一交易保存回覆、完成填寫代辦，
並新增「複習課程（日期）：原始回覆」代辦；複習須由本人以 `/done` 完成。
兩種代辦不自行指定截止日、不呼叫 Gemini；摘要須走獨立的明確選檔流程。
2026-10-05 補充：完整回覆為「我也不知道」「不確定上到哪裡」等明確未知進度時，
調查仍視為已回答，停止催填並完成填寫代辦；改建「確認課程今日上課範圍（日期）」
代辦，不建複習代辦。保留原始回答。不以包含「不懂／不確定」等關鍵字就判定未知，
例如「第三章有些地方不懂」仍是有範圍的複習。採保守的完整句型規則，不宣稱通用語意理解。
同一 session 或 Telegram update 重送不得重複新增；舊 session 不回填代辦。
若填寫代辦已被清除，不重新建立，但有效回覆仍可新增一次複習代辦。
跨日未回覆仍沿用 missed 規則，填寫代辦保持未完成，可自行 `/done` 結案，
不得假裝已填寫或已複習。校曆停課判斷尚未整合至排程。

若未回覆：

- 第一次提醒：初始通知後一小時
- 第二次提醒：再過一小時
- 最多提醒兩次
- 當日結束後，未回答的 session 標記為 `missed`
- 不跨日持續打擾

### 3.3 File selection

收到進度後，Chronos：

1. 登入 TronClass。
2. 找到對應課程。
3. 第一版列出已由 browser-session 觀察到的 PDF。明確標示「已觀察教材，可能不完整；上傳時間未知」，不得宣稱完整或已依最新上傳排序。
4. 使用 Telegram inline buttons 讓 YiLi 選擇。
5. 允許一次選擇多個 PDF。
6. 按下「完成選擇」後開始處理。

即使系統推測出最可能的檔案，也不得直接替 YiLi 決定。

2026-10-04 範圍決定（YiLi 已確認）：完整課程教材枚舉與依真實上傳時間由新到舊排序延後，不屬於第一版 Phase 2 完成門檻。不得用檔名、ID 或本機觀察時間冒充上傳時間。清單新鮮度檢查、明確多選確認、PDF 完整性、內容品質、Firestore 保存與 Markdown 匯出仍須驗收；此決定不代表 Phase 2 已完成。

### 3.4 Summary output

每份 PDF 獨立進行逐段重點提取，不再開發或產生跨 PDF 合併摘要。內容包括：

1. 今日課程範圍
2. 核心概念
3. 概念之間的關係
4. 教授可能考的內容
5. 為什麼可能成為考點
6. 需要回頭確認的模糊內容
7. 原始檔案名稱與涵蓋頁碼

Output rules:

- 中文解釋，保留自然英文 technical terms
- Telegram 分段傳送
- 預設每段 3 個實體頁碼（不是投影片印刷頁碼）；明確概念邊界可調成 2 或 4 頁，尾段允許 1–2 頁。不得漏頁、重疊或跨檔案。
- 不設定 1,500–2,500 字的最低篇幅；依證據提取重點，不為湊字數擴寫。
- 每段標示檔名、原始實體頁碼、段次／總段數；確認選檔後依序自動生成、保存、傳送，不逐段詢問。
- 每段先保存 canonical Markdown，再傳 Telegram；已完成的段落不得重生或重送。未知傳送結果暫停，不冒險重送。
- 分頁規則版本與頁碼範圍納入內容識別；目前自動流程使用確定性的 3 頁分段，未接入語意邊界偵測。
- 「可能考」必須標記為 inference
- 沒有講義證據時不得捏造教授偏好
- 每項考點推論須附引用頁面的原文摘錄；本機核對摘錄是否存在。引用存在不代表推論成立，仍須核對語意。
- 生成後以同一段原始 PDF 進行一次內容審查，修正不受支持的主張與遺漏的條件，再通過本機驗證才保存、傳送。每次嘗試最多兩次模型請求（初稿及審查），審查失敗不得退回發布未審查初稿；不採用無限修正迴圈。模型審查不等於保證正確，正式驗收仍須比對來源。
- Gemini 無法完成時，通知失敗並稍後有限次重試

---

## 4. Notes and Markdown

摘要的 canonical record 存入 Firestore，包含原始 Markdown content。

建議 Telegram commands：

- `/notes`：列出最近的課程摘要
- `/notes 軟體工程`：列出指定課程摘要
- `/note 編號`：閱讀摘要
- `/export 編號`：動態產生並下載 `.md` 檔

Cloud Run container filesystem 不作為持久儲存。

每筆摘要至少包含：

- course
- class date
- reported progress
- selected TronClass files
- source file metadata
- Markdown content
- Gemini model
- generation status
- created timestamp
- last error
- content fingerprint

相同檔案與進度不得因 webhook retry 重複生成。

---

## 5. TronClass monitoring

### 5.1 Polling schedule

每天在以下時間檢查：

- 08:00
- 12:00
- 18:00
- 22:00

YiLi 回覆課程進度後，額外立即檢查該課程。

### 5.2 Supported content

MVP 處理：

- PDF
- 課程公告
- 作業說明
- 作業附件
- 課堂影片或錄影的存在狀態

影片規則：

- 只通知有新影片
- 不下載
- 不轉錄
- 不送入 Gemini
- 提供 TronClass 原始入口

### 5.3 Authentication

- 2026-10-05 YiLi 已確認改採 Cloud Run 雲端收集，允許必要登入 session 安全保存於雲端，以便個人電腦關機後仍可監測。Chrome extension／local companion 保留為手動驗證路徑，不再是正式自動監測的必要常駐依賴。不以不可靠的 CAS REST 密碼登入作為正式路徑。
- 雲端收集採獨立 Cloud Run Job，以 Cloud Scheduler 固定觸發，並由已授權服務在進度回覆後觸發；與 Telegram webhook service 分離，不依靠 HTTP response 結束後的背景執行緒。
- 不保存 CAS 密碼。僅搬移經確認必要的 session，保存在 Secret Manager，使用專用 service account 與單一 secret 範圍的最小存取權限；Telegram webhook service 不得因此取得 session 讀取權限。
- 不將 cookie、session 或完整瀏覽器 profile 寫入 Git、Firestore、一般 log、Telegram、容器映像或部署參數。Cloud Run 暫存檔不是持久儲存；session 失效或撤銷後不可用舊版本自動回退。
- 初次導入、輪替與撤銷 session 必須有不顯示內容的受控流程。雲端登入是否受 IP／裝置綁定影響仍需真實驗證，授權搬移不等於已證明可用。
- Session 失效時回報 `reauth_required`，由 YiLi 在瀏覽器重新登入；cold start 不得宣稱已登入。
- 重新登入後重新觀察並驗證 session，不重用過期的就緒狀態。
- 不反覆嘗試密碼登入；不可取得的附件明確標記 `deferred_attachment`，不得冒充已保存。

CAS 或 TronClass 頁面結構改變時，系統必須明確回報 `tronclass_adapter_failed`，不得將抓取不到誤判為沒有新內容。

---

## 6. Assignment workflow

### 6.1 Discovery

發現新作業時：

1. 取得課程、標題、說明、附件與 deadline。
2. 建立 Chronos Assignment task。
3. 立即發送 Telegram 通知。
4. 保存 TronClass source identifier，防止重複建立。

若沒有明確 deadline：

- 不自行猜測
- Telegram 詢問 YiLi
- 在確認前標示 `deadline_pending`

明確截止時間可使用 `/deadline 作業固定ID YYYY-MM-DD HH:MM` 確認，時區為
Asia/Taipei；固定 ID 由作業通知提供，和 `/tasks` 清單順位不同。無效日期或缺少
時間時不更新，已完成的作業不得重設截止時間。此指令不經 AI 日期推測。

### 6.2 Reminder schedule

有 deadline 的作業在以下時間提醒：

- 截止前 7 天
- 截止前 3 天
- 截止前 1 天
- 截止前 3 小時

使用既有 `/done 編號` 完成作業後：

- 停止所有後續提醒
- 保留作業資料與草稿
- 記錄完成時間
- 不刪除歷史

### 6.3 Draft generation

發現作業時不自動生成草稿。

YiLi 按「準備作業」或使用：

```text
/prepare 編號
```

才啟動 Gemini。

報告型輸出：

- requirements breakdown
- recommended structure
- arguments and supporting points
- first draft
- references or facts requiring verification
- final review checklist

程式型輸出：

- requirements breakdown
- implementation plan
- starter code
- test plan
- edge cases
- assumptions and unresolved questions

所有草稿必須：

- 明確標示推測與待驗證內容
- 不自動提交作業
- 不偽造實驗結果、引用或執行證據
- 保留 YiLi 必須親自判斷與完成的部分

---

## 7. Academic calendar integration

Source of truth:

- 國立臺灣海洋大學官方行事曆
- 115 學年度官方校曆頁面或 PDF

System behavior:

- 每日同步官方校曆
- 保存 source URL、抓取時間與內容 fingerprint
- 只根據明確的「放假」「補假」「停止上課」判定停課
- 「正常上班上課」不得判定為放假
- 「教師自行擇期補課」等不明確事件交由 YiLi 確認

### 7.1 Holiday notifications

如果隔天是放假日：

- 前一天 12:00 通知 YiLi
- 當天 08:00 再提醒一次
- 停止當日所有課後進度詢問

若臨時放假消息在前一天 12:00 後才出現：

- 下一次同步發現後立即通知
- 不等待隔天 08:00

### 7.2 Exam weeks

進入官方期中或期末考週後：

- 暫停一般課後進度詢問
- 每天早上發送當日考試與複習事項
- 不因「考試週」自行推測哪門課當天考試

考試週開始前七天，Chronos 應要求 YiLi 確認：

- 科目
- 日期與時間
- 地點
- 考試範圍
- 尚未完成的複習項目

若資料不足，訊息必須顯示「尚未提供」，不可生成虛構考試安排。

---

## 8. AI and cost policy

AI provider:

- Gemini
- 優先使用可滿足 PDF summarization 的 free-tier model
- 不使用 OpenAI API
- 不設付費模型 fallback

Cost controls:

- Cloud Run `min-instances=0`
- 收集 Job 採單 task、parallelism=1、明確 timeout 與預設零自動重試；跨 execution 仍須使用資料庫 claim 避免重疊。Job 完成後退出，不維持常駐瀏覽器。
- 雲端 session 與收集授權不等於接受額外費用。Cloud Run、Secret Manager、Scheduler、映像儲存與流量成本均需列入估算；不自動開通新付費方案，不保證免費。
- 限制最大 instance 數量
- 只在明確任務下呼叫 Gemini
- 以檔案 fingerprint 快取摘要
- 相同教材不重複處理
- 記錄每日 request count 與估算 tokens
- 接近免費配額時通知 YiLi
- 超出配置上限時停止 AI 工作並回報原因

「0 元」是 operating target，不是雲端供應商的永久價格保證。產品不得宣稱 guaranteed free。

---

## 9. Reliability requirements

- Telegram webhook update 必須 idempotent。
- Assignment、summary、notification 都必須具有 deduplication key。
- 已送達但 response 不明確時，不得直接重新生成 AI 內容。
- Firestore 是 production source of truth。
- 每次 retry 必須保存 attempt count、last error 與 next retry time。
- TronClass、Gemini、Telegram、Firestore 各自回報獨立健康狀態。
- `/health` 成功不代表所有外部整合正常。
- 所有日期時間使用 `Asia/Taipei`。

---

## 10. MVP acceptance criteria

MVP 完成必須證明：

1. 七門課會在下課五分鐘後發送正確訊息。
2. 未回覆時只追加兩次提醒。
3. Reply 能正確綁定課程與日期。
4. 可從 TronClass 列出正確課程的 PDF。
5. 可多選 PDF，按各檔案約三個實體頁碼逐段生成、保存並傳送重點；不產生合併摘要。
6. 摘要可從 Firestore 重新取得及匯出 Markdown。
7. 新作業只建立一次。
8. 缺少 deadline 時會詢問，不會猜測。
9. `/done 編號` 會停止作業提醒。
10. `/prepare 編號` 可產生報告型或程式型草稿。
11. 新影片只提醒，不下載。
12. 官方假日會在前一天 12:00 與當天 08:00 通知。
13. 假日不會發送課後進度問題。
14. 考試週改發複習訊息，不捏造考試資料。
15. 登入、解析或 AI 失敗時會明確通知並有限重試。
16. 錯誤訊息及 log 不包含帳密、cookie、Telegram token 或 API key。
17. Deployment 後需分別驗證 Cloud Run、Firestore、Scheduler、Telegram、Gemini 與 TronClass，不以單一 health check 代替。

---

## 11. Delivery phases

### Phase 0 — Feasibility spike

只驗證、不建立完整功能：

- CAS 自動登入
- TronClass course mapping
- 公告、作業與附件的可解析性
- PDF 下載權限
- 官方校曆解析穩定性

**Gate:** 登入與資料取得可重複成功，才能進入 Phase 1。

### Phase 1 — Course tracking

- 課表
- 下課詢問
- Reply correlation
- 重問機制
- Firestore state

### Phase 2 — Materials and summaries

- Observed PDF listing with explicit partial-coverage and unknown-upload-time labels
- Multi-select
- Gemini per-PDF paginated key-point extraction, durable per-segment delivery
- Markdown export

Deferred beyond the first release: complete course enumeration and verified
newest-upload-first ordering (owner-approved on 2026-10-04). This deferral does
not relax note grounding, explicit selection, persistence or export acceptance.

### Phase 3 — Assignments

- Assignment discovery
- Deadline confirmation
- Reminder lifecycle
- `/prepare`
- `/done` integration

### Phase 4 — Calendar intelligence

- Official calendar sync
- Holiday suppression
- Previous-day notification
- Exam-week mode

### Phase 5 — Production verification

- Focused tests
- Secret-safe deployment
- Real owner-only Telegram test
- Live TronClass retrieval
- Duplicate webhook test
- Failure and retry test
- Cost observation

Implementation 不得跳過 Phase 0。
