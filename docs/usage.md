# Gemini 與 Telegram 指令參考

Web 與 Telegram 的自然語言新增、Telegram 改期共用 Gemini API，不啟動本地模型。程式呼叫 Gemini 原生 `generateContent` endpoint，要求 JSON response 並再次驗證輸出 schema。

在既有 `.env` 加入以下設定（不要覆蓋原有內容）：

```dotenv
CHRONOS_GEMINI_API_BASE=https://generativelanguage.googleapis.com/v1beta
CHRONOS_GEMINI_API_KEY=你的Gemini API金鑰
CHRONOS_GEMINI_MODEL=gemini-3.8-flash
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
/clear
```

除了 slash commands 外，直接傳送中英文自然語言就會新增一筆代辦。Telegram 與 Web 的互動文字統一使用英文。
`/tasks` 依「期限最早、無期限最後、同期限較早建立者優先」排序，並將目前未完成代辦動態編為 `1..n`；永久 database ID 不會顯示。`/done` 與 `/edit` 接受當下位置，但 mutation 會在 receipt transaction 內綁定永久 ID，因此清單重新排序不會讓已保存的 retry 改到另一筆 task。不存在的位置會顯示英文錯誤及最新清單。每天 08:00 的清單使用同一格式。
`/edit <position> <instruction>` 接受中文或英文自然語言，可同時修改標題、期限與分類，也能明確移除期限或分類；未提及的欄位會保留，儲存標題仍為精簡英文 action phrase。移除日期、移除分類與保存 tag abbreviation 等明確操作會走 deterministic path；其他語意改寫才呼叫 AI。任務改期與作業截止日期都統一使用 `/edit`；`/reschedule` 與 `/deadline` 不再支援。
AI update service 暫時不可用時，task 不會變更，原始 command 會持久保存，Telegram 會提供 **Retry editing Task n** 與 **Cancel saved edit for Task n**。Retry 依永久 task ID 執行；如果目標已完成或刪除，會 fail closed。成功更新分成兩則訊息：第一則只列出 changed fields，第二則才顯示更新後清單。兩則訊息分別保存 delivery progress，第二則失敗時不會主動重送已確認成功的第一則。
舊的中文 commands 與 `/postpone` 不再支援；`/start` 與 `/help` 都會顯示英文使用說明。
`/clear` 先顯示確認訊息；只有按下 **Delete all tasks** 才會刪除所有 open 與 completed task records，按 **Cancel** 不會變更資料。Telegram update receipts 與內部 ID counter 不在清除範圍內，以維持 webhook idempotency 與 ID 唯一性。

### Study 指令範例（需額外設定）

Study 預設關閉，包含 NTOU / TronClass 特定課表、課程 ID 與雲端專案綁定；不是跨校通用 LMS 整合。請先讀 [Study 文件入口](README.md)。

作業固定 ID 會直接出現在作業通知，例如 `新作業 #27`。這個 `27` 只供 `/prepare` 與 `/draft` 使用；修改截止日期時，先用 `/tasks` 找到當下順位，再用 `/edit`。`/assignment`、`/exam` 與 `/exams` 不再支援。

`/classday` 接受中文或英文自然語言，用於確認特定課程在某天是否上課：

```text
/classday 10/07 軟體工程不上課
```

筆記範例以「作業系統」課程為例。先列出該課程已保存的 notes，再複製回覆中的完整 64 字元 note ID：

```text
/notes 作業系統
/note <從上一個回覆取得的完整 note ID>
/export <同一個完整 note ID>
```

`/note` 讀取內容，`/export` 則傳送同一份 canonical note 的 Markdown 文件。Repository 不提供固定示範 ID，因為 note ID 是由實際內容計算，寫死的 ID 可能指向不存在或不同的筆記。

`/tasks` 與每日清單使用粗體 `Tasks` 標題。每筆 task 的 title、完整 `YYYY-MM-DD HH:MM` due time 與 tag 各自位於不同文字層級，避免把 metadata 混入標題。過長的多字 tag 只在顯示層縮寫，例如 canonical `computer-architecture` 顯示為 `#CA`；canonical value 仍保留供搜尋與資料關聯。Owner 也能透過 `/edit` 保存自己的 tag display preference。課後回覆原文仍保存作為教材與摘要依據，但新建立的複習代辦會另外使用一次 AI 產生簡短英文 label；若翻譯暫時失敗，進度仍會保存，複習代辦回退顯示原文。

設定 `CHRONOS_TELEGRAM_CHAT_ID` 後，其他 chat 無法操作 bot。

Webhook 會驗證 JSON 結構與整數 `update_id`，合法的非文字更新會略過。每筆 message 或 callback 的代辦變更與處理紀錄會一起儲存在 SQLite 或 Firestore；動態位置會在同一 transaction 內解析成永久 ID，重送同一 update 不會重複新增、完成、修改、改期或清除，重啟後仍有效。多訊息回覆會逐則保存 delivery progress，後續 webhook retry 從第一則未確認訊息繼續。若 Telegram 已收到某則訊息而程式尚未記錄送達就中斷，該則文字仍可能重複，但代辦不會重複變更。去重與 pending-edit 紀錄目前不會自動清除；完成或取消 retry 會刪除對應 pending edit。


回到 [本機啟動](getting-started.md) 或 [README](../README.md)。
