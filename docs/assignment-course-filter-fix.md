> 歷史紀錄：以下雲端操作與驗證屬 2026-10-14；2026-10-21 僅整合程式碼與離線測試，未重新部署或驗證正式服務。部署覆蓋包保留於原電腦，未加入 Git。

# 非追蹤課程作業篩選修正（2026-10-14）

使用者回報早上收到大量非本學期作業通知。截圖中的課程 172796、170060、172823 不在 `assignment_bridge.TRACKED_COURSES` 清單內。

## 已確認原因與修正範圍

觀察資料匯入已有課程 allowlist，但 `tick_assignments` 未核對資料庫既有作業的課程，兩個持久層的 `list_open_tasks` 也會直接列出所有未完成任務。因此已存在資料庫的非追蹤課程作業仍會通知及列入代辦。

通知現在於 claim 前及發送前檢查課程；SQLite / Firestore 清單根據 assignment 的來源課程與 task ID 排除非追蹤課程。不是用可編輯的 project 或標題判斷。保留原始作業、任務與完成狀態，不刪資料，也不影響一般手動任務。這份清單是既有的七門課設定，不是自動辨識學期；換學期仍需更新課程設定。

本次未確認是哪個外部寫入流程建立這些作業，也未修改 submitted / unknown 的通知政策。已送出的 Telegram 訊息不會被撤回。

## 驗證與部署證據

- 原工作目錄基準：`7fe9eee`。本機相關測試共 74 項通過。
- 原 Cloud Run：`chronos-00070-8bb`，提交標記 `b4832b135f4ec17155257a629cc5ef8204a4cd10`。已下載其 Cloud Build 原始碼，核對 Python 程式、Dockerfile、pyproject.toml、uv.lock 與該提交相同。
- 為避免覆蓋雲端新功能，另在該提交的隔離工作目錄套用修正；73 項相關測試通過（SQLite 與 Firestore fake，不是真實 Telegram 投遞）。修正提交 `639cdd5`，分支 `codex/assignment-course-scope`，尚未推送。
- 真實 Firestore 唯讀核對：未完成 25 筆，篩選後 10 筆；排除 15 筆，課程分布為 172823：10、172796：3、170051：1、170060：1。核對確認其餘任務完整保留，未寫入資料或發通知。
- 新 Cloud Run：`chronos-00071-cbr`，已確認 100% 正式流量；`/health` 回傳 ok（僅表示服務回應，不是外部整合測試）。

原工作目錄保留相同修正供檢閱；實際部署使用上述雲端基準的隔離工作目錄，不可從較舊的原工作目錄直接重新部署整個服務。

## 使用者回報仍顯示後的處理

再次確認正式流量為 `chronos-00071-cbr`；Telegram getWebhookInfo 指向正式服務、pending updates 為 0。經驗證的正式 `/api/tasks` 回傳 10 筆，沒有上述舊課程。未確認使用者截圖訊息的產生時間，不將截圖逕行判定為舊訊息。

使用者要求實際移除後，以單一 Firestore 交易將已核對的 15 筆非本學期未完成代辦備份到 `chronos_quarantined_tasks`，再從 `chronos_tasks` 刪除；保留 assignment 來源紀錄，避免同一來源重新建立任務。交易先讀後寫、限定四個已核對課程、核對筆數為 15，已有備份時停止。完成後直接讀取 tasks，未完成共 10 筆、上述舊課程共 0 筆。未發送或刪除 Telegram 訊息。

## 根因追查及掃描器修正完成

2026-10-14 後續追查已找到來源，不再是未確認：Cloud Scheduler
`chronos-tronclass-weekly-scan` 實際每天台北時間 06:00 呼叫獨立 Cloud Run Job
`chronos-assignment-scan`。更新 Web 服務不會更新此工作。

今早執行 `chronos-assignment-scan-6lvdz` 的雲端紀錄明確顯示
`created=15, assignment_notices_sent=15, baseline_skipped=0`。
實際 TronClass `/api/my-semesters` 將舊學期 `1141`（ID 446）標成
`is_active=true`，正確 `1151`（ID 468）反而為 false。原收集器信任此旗標，
因此抓到舊課程；原匯入器只在首次初始化略過已繳交／過期作業，後續就放行。

已從原工作建置來源還原程式，建立最小覆蓋映像，原始碼與操作說明保存在
本機 deploy/assignment-scanner-fix/README.md（歷史部署覆蓋包）。現在明確選擇 1151，
檢查課程自身學期及七門追蹤清單，僅接受明確 `submitted: false` 的來源；
每次新增都排除已繳交／過期作業，匯入及通知亦核對課程。

- 九項掃描器回歸測試通過，包含重複執行及唯讀模式無副作用。
- Job 已部署映像 `sha256:fca66f4c16ceb9faccbe13aebbf0e02a53ddd50e53192910d068f16141dad301`。
- 真實雲端驗證執行 `chronos-assignment-scan-vlrqf` 成功：
  `semester=1151, observed=2, eligible_new=1, outside_scope=0`；
  寫入與 Telegram 發送均為 0。這是實際登入來源與讀取正式資料庫的 dry run，
  不是 mock，也不是 Telegram 投遞測試。
- 已確認正式每日 06:00 排程仍啟用並指向此 Job；dry-run override 僅用於該次
  驗證，沒有保存在日常工作設定。未為驗證額外發送訊息。

本次錯誤來源、資料清理與防止重複通知均已處理；未宣稱未來外部服務永遠不會
變更。換學期仍需同步更新明確學期與追蹤課程，不能重新信任 `is_active`。
