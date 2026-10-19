# Backward Planning：期限倒推與拆解

在開始前看見交付成果、預估工時與真正的時間缺口：期限不變，但剩餘工作可能已經排不進去。

## Telegram 使用

```text
/help backward
/backward 下週五要交資料庫報告
/backward #42
/backward #42 已完成資料蒐集，每天最多90分鐘
/backward progress
/backward progress 42
```

`/backward #42` 使用任務固定 ID，沿用目標與期限，不是 `/tasks` 清單位置。
自然語言會把既有 open tasks 提供給 AI 配對；程式驗證返回的 ID，完全相同標題也會重用。
模糊配對仍可能需要使用者指出 ID；固定 ID 最可靠。已有計畫的目標顯示進度，保留已完成工作。

缺少期限、目標或無法表達的重要限制時，先問簡短問題，直接回答或回覆卡片即可。
未知報告要求可列為「確認要求」步驟，不能編造頁數、評分標準或引用數量。
預覽分開顯示原始輸入、已回報進度、AI 工時估計及假設；未回報進度是 unknown。
每步都有產出、工時、依賴編號與建議工作時段。

- **Confirm schedule**：重查 Calendar、課表及容量，才建立目標（必要時）、子任務與活動。
- **Adjust plan**：送出新條件，重新預覽與確認，舊按鈕失效。
- **Cancel**，或修改／釐清期間 `/cancel`：取消草稿，沒有正式任務與 Calendar 寫入。

預覽有效兩小時，之前只保存草稿及 Telegram receipt。
若修改既有目標期限，預覽明示原期限與新期限，確認後才沿用任務編輯機制更新。
拆解的是 remaining work；請補充已完成的部分。

## 日期與容量

- 固定 **Asia/Taipei**。`下週五` 是下一個週一開始的星期五，`這週五` 是本週。
  常用中文相對日期由程式解析；其他表達由 Gemini 結構化解析，再驗證日期與時區。
- **只有日期不代表已知繳交時間**。保守規則：工作及緩衝在前一天結束前完成；預覽明示。
  確定當天 17:00 截止時，Adjust plan 補充日期與時間，取得新預覽。
- 期限須在未來 60 天內；過期不硬排，遠期目標先選近期里程碑。
- 沿用 `CHRONOS_LEARNING_START_HOUR=9`、`CHRONOS_LEARNING_END_HOUR=21`、
  `CHRONOS_LEARNING_DAILY_MINUTES=540`。日容量包含課程與已核准學習區塊；完成任務不會歸零。
  可要求更小 daily cap，提高硬上限需修改現有設定。
- 沿用課表 policy、Google Calendar 的 busy calendars、12:00–13:00 及 17:00–18:00 用餐限制。
  規劃最小單位 15 分鐘，不占用只允許十分鐘任務的用餐空檔。
- AI 估時向上取整至 15 分鐘，最多八個有產出步驟，每步可跨多個工作時段；活動不超過三小時。
  依拓樸順序串行安排，獨立步驟也不會同時占用一個人的時間。
- 緩衝為工時的 20%，取整至 15 分鐘，至少 30 分鐘，實際保留為最後的 Calendar buffer。
  這是 planning policy，不是 AI 對任務風險的量測。
- latest-first 從期限往回安排，容量不足顯示包含緩衝的分鐘缺口，不提供確認按鈕。
  可縮小範圍、修正估時、調整每日容量或協商期限。
- Calendar 不可用時不宣稱已確認空檔，也不以靜態課表假裝讀到 Calendar。

## 持久化與恢復

重用 `task_action_state`、repository transactions、Calendar transport、Telegram update receipts，
SQLite 與 Firestore 使用同一流程，沒有第二份任務資料庫或獨立 scheduler。

確認先取得 writer lease，重查完整計畫，再以 transaction 保留時段與既有 `learning:day:*` 日容量。
學習排程確認與 `/free` 確認也避開這些 reservations。
每個正式任務、關係及 scheduler 管理標記原子保存；Firestore 的 reads 全部先於 writes。
目標與步驟標為 backward managed，原本單一區塊 scheduler 不會再替它們建立重複區塊。

Calendar 與資料庫不能跨系統交易，因此使用 resumable saga：活動 ID 由計畫 token 與時段序號決定。
重試先讀取各 ID、確認 ownership、狀態與時段，才補缺少活動；回應遺失不直接重建。
每筆成功獨立記錄；部分失敗顯示已記錄的步驟與區塊數，最後一筆可能需要 reconciliation。
資料庫部分失敗且仍能讀取狀態時，回報已成功的步驟與區塊數；整個狀態儲存不可用時，
webhook 回傳可重試錯誤。已提交任務與關係保留。

已核准但未完成時，原卡片 **Confirm schedule** 安全重試；Cancel 不會刪除已寫入資料。
另一確認執行中就稍後重按，服務中斷後 writer lease 最多十分鐘。
若 owner 修改／取消已建立活動，或剩餘時段新增衝突，恢復停止並保留資料，不覆寫、不刪除、
不自動換時段；需先檢查既有安排。

`/backward progress ID` 可由目標或步驟查看關係與比例，省略 ID 查看最近計畫。
用法統一由 `/help backward` 或 `/help` → **Backward Planning** 查看。
單獨 `/backward` 只提示前往 Help，Telegram 指令選單不再列出它。
用 `/tasks` 查當下位置，再 `/done <position>`
完成一步；不會自動完成目標。目標也由使用者明確完成。

## 設定、限制與驗證

沿用 [Calendar 授權與啟用](learning-calendar.md)，需 `CHRONOS_ENABLE_LEARNING_CALENDAR=true`。
不新增 OAuth scope、scheduler 或 secrets；沿用 [發布流程](deployment.md)。
第一版支援拆解、預覽、修改草稿、確認建立與進度查詢；不提供已建立計畫整份改寫、
自動持續重排、耗時學習回饋或批次取消活動。

不搜尋／下載 TronClass 簡報：公開 runtime 沒有學校帳密或教材下載能力。
需要依簡報規劃時，請提供範圍或先執行查閱 TronClass 步驟，不能聲稱已讀教材。
Calendar 不提供原子的「檢查空檔並插入」；每筆插入前重查仍不能鎖住其他 Calendar clients。

`tests/test_backward_planning.py` 以 SQLite、禁止 read-after-write 的 Firestore test double、
Telegram webhook 測試日期精度、相對日期、過期、容量、依賴、作息、故障、時段與任務變更、
取消與修改、重送、重複確認、lease、部分資料庫／Calendar 失敗、未知結果恢復、
被修改活動、目標重用與獨立完成進度。無真實外部呼叫，不等於 production 端到端驗證。
