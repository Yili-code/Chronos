# 專案記憶

最後更新：2026-10-14

## 本次回報：TronClass 作業掃描誤通知

- 使用者回報掃描錯誤的課程再次被當成新作業傳到 Telegram；附件截圖只是部分通知內容。
- 截圖中的通知文字聲稱 TronClass 頁面顯示已繳交，並提醒由使用者確認是否結案；同一截圖也看到相同課程編號 `172823` 的兩個不同作業通知。這些是截圖內容，尚未由 TronClass 現場或來源資料驗證。
- 目前程式的核心風險已定位：`chronos/assignment_bridge.py` 的觀察契約僅接受 `submission_status` 為 `submitted` 或 `unknown`，沒有「未繳交」狀態；`chronos/assignment_sync.py` 會將兩種有效觀察都匯入作業資料；`chronos/assignment_scheduler.py` 對未完成記錄仍會發送首次「新作業」通知，即使來源標示 `submitted`，只在訊息中加上「是否結案仍由你確認」。
- 因此現行設計無法排除已繳交作業被當成新作業通知，也無法在掃描錯誤或頁面狀態判讀錯誤時保證不通知。通知可能反映已觀察到的作業資料，不代表已判斷該課程／作業確實是新作業。
- 作業身分以 `course_id + source_id` 為準，雜湊後作為 key；相同來源身分應更新既有記錄，不應只因標題／截止時間變更而新增。若通知出現不同編號，需檢查瀏覽器擷取的 `source_id` 是否穩定且對應 TronClass 真正作業 ID，以及觀察頁面是否為正確課程。
- 下一步修正方向：讓掃描證據能明確表示 `unsubmitted`、`submitted`、`unknown` 及必要的錯誤狀態；只有明確且可信的未繳交作業可建立新待辦／發首次新作業通知。已繳交項目應略過新作業通知（或只更新既有記錄而不復活已完成項目）；未知、錯誤或未支援頁面一律 fail closed，不建立或通知。保留錯誤／觀察狀態供診斷，不可把錯誤頁面當空清單。
- 修正前需檢查 Chrome extension 擷取 DOM 與送出觀察的程式碼、SQLite/Firestore 的狀態遷移，以及同步和 Telegram 通知路徑；為 submitted、unknown、錯誤頁、重複掃描與 source ID 穩定性補測試。完成程式修正不等於已更新雲端或瀏覽器 extension；部署與現場驗證須分別記錄。

## 維護注意

- Study 模組綁定特定 NTOU / TronClass 課程與設定，預設關閉；不要將其描述成通用 LMS 整合。
- 作業通知、公告及 collection 的既有文件可能是階段性工程紀錄。判斷現況以程式與最新驗證為準，並清楚區分本機測試、production 部署及真實瀏覽器／Telegram 驗證。
- 不在記憶或日誌中保存帳密、cookie、token 或帶權限的附件 URL。
