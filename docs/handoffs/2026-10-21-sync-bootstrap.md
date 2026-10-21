# 跨對話同步設定

日期：2026-10-21（Asia/Taipei）
專案：chronos；設定前遠端基準：e874041

已實作：SessionStart 與 UserPromptSubmit 同步檢查、AGENTS 交接規則。
驗證：共用同步程式在隔離 Git repository 測試；不代表應用程式或部署測試。
待使用者操作：各電腦取得設定，依 Codex hook 審查介面信任設定。
下一步：讀取本次任務相關實作並建立獨立交接檔。

## 設定開始時本機未提交狀態（歷史快照，請重新檢查）

```text
M chronos/ai.py
 M chronos/assignment_scheduler.py
 M chronos/db.py
 M chronos/firestore_db.py
 M chronos/main.py
 M chronos/telegram.py
 M docs/usage.md
 M tests/test_assignment_metadata.py
 M tests/test_assignment_scheduler.py
 M tests/test_assignment_storage.py
 M tests/test_assignment_sync.py
?? .deploy-notice/
?? AGENTS.md
?? chronos/image_deadlines.py
?? deploy/
?? docs/assignment-course-filter-fix.md
?? docs/project-memory.md
?? tests/test_image_deadlines.py
```

本機程式與遠端更新重疊，已保留原工作目錄；設定從遠端基準的獨立工作目錄發布。此紀錄不表示本機程式已整合。
