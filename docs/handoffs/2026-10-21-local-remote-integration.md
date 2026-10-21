# 本機與遠端整合

更新：2026-10-21（Asia/Taipei）

整合本機圖片期限確認、課程篩選與英文 HTML 通知；保留遠端 AI 使用量追蹤、首次發現才通知及 Open/Edit 按鈕。舊版更新/期限重複通知已由遠端 6ec9001 明確取消，本次不重新啟用。
驗證：完整測試 1132 passed、2 failed（舊假課程 ID 被篩除）；僅修正測試資料後，task_actions 與 assignment_scheduler 共 27 passed。未再次跑整套。無真實 Telegram/Gemini/Calendar 呼叫或部署。
原始本機快照保留於 stash e41e188edcdd6409a84b327c3ad5a73ff52a287a，尚未刪除。
.deploy-notice/ 與 deploy/ 是既有本機部署暫存/覆蓋包，保留原位置並以本機 info/exclude 排除，未當作現行程式發布。

狀態：實作已整合並準備提交；是否已推送請核對本檔所屬提交與追蹤分支。未部署 production。
下一步：新任務先執行同步檢查並閱讀相關實作；不要套回整份舊 stash 覆蓋新版。
