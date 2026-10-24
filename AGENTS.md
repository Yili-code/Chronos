<!-- BEGIN CODEX CROSS-SESSION SYNC -->
## 多對話與跨電腦交接（2026-10-21）

這一節是目前的交接規則；下方有日期的舊摘要保留作歷史導覽，不能當成目前進度。使用者最新明確指示優先。

### 每次開始或恢復工作
- 先確認本次 SessionStart / UserPromptSubmit hook 的同步結果。若未執行，從專案根目錄執行 `python .codex/session_sync.py --manual`（macOS/Linux 可用 python3）。
- hook 只 fetch 並在工作目錄乾淨且歷史可快轉時更新追蹤分支。不會 commit、push、stash、reset 或覆蓋衝突。
- 同步失敗、遠端領先且本機有修改、歷史分歧時，先處理同步問題；可做唯讀診斷，不可把狀態說成已同步。離線工作需明確標記。
- 同步後重新讀取本檔、PROJECT_STATUS.md、相關 docs/handoffs/ 紀錄，再核對實際檔案、近期提交與未提交修改。不要單憑聊天記憶重做已完成的功能。
- 實作以檔案與驗證為準；需求以最新明確指示為準；記錄衝突要指出，不臆測完成。歷史摘要中的測試與部署結果不適用於新版本。

### 每個任務自動留下紀錄
- 開始實作時建立獨立 `docs/handoffs/<日期>-<任務>-<唯一識別>.md`，記錄工作範圍、分支與涉及檔案；先查看其他 active 任務，避免重疊編輯。
- 在重要決策、可驗證階段完成，以及每次有實質進展的最終回覆前更新自己的紀錄，不需使用者另貼同步指令。
- 記錄含時區的更新時間、基準提交、目標、已實作/已驗證/未完成、取消的方案、驗證結果、下一步、commit/push 狀態。不可把中斷工作標成完成。
- PROJECT_STATUS.md 只放共同目標、穩定決策與交接目錄入口；詳細進度分散於各任務檔。修改共用文件前重讀，保留他人更新。
- 完成可交接工作時，檢查並只提交本任務相關且可分享的檔案，連同交接紀錄；沿用既有分支/PR 流程並推送已設定的遠端。若含其他任務未發布提交、未解決衝突或無法驗證內容，先回報，不能盲目全部加入。
- 不提交秘密、私人資料、原始聊天或無關修改；不 force push、不自動丟棄修改。雲端程式部署與 Git 同步不同，未另獲授權不執行部署。

### 自動化限制
- `.codex/hooks.json` 設定新建、恢復、分叉、壓縮後及每次新提示的同步檢查；需要本機 Codex 支援、Python 3、Git、遠端存取及專案/hook 信任。
- 新或變更的 hook 需要使用者在 Codex 的 hook 審查介面信任；CLI 可使用 `/hooks`。不得自行修改信任紀錄或繞過審查。
- 其他電腦必須先取得這些檔案並信任 hook。已開啟的聊天應重新讀取指引，不能假設即時載入。
- 檔案規則與同步鎖不是跨對話編輯鎖。多任務重疊時使用獨立分支/worktree，並明確整合。
<!-- END CODEX CROSS-SESSION SYNC -->


## 必須沿用的驗證與部署流程（2026-10-21）

使用者已要求將此流程持久化並在後續工作自動套用。此節優先於下方歷史慣例，不需每次重新詢問是否執行檢查。

- 後續程式變更使用 `codex/` 分支與 PR，不直接推送 `main`。完整流程為：本機驗證 → 推送 PR → CI 全綠 → 合併 → 在已有部署授權下部署通過驗證的提交。此流程本身不授權部署。
- 修改 AI 意圖判斷、Telegram 分派、資料庫、共用 fixture 或其他跨模組共用流程時，推送前必須跑完整 Python 測試；局部測試只作開發回饋，不能取代完整驗證。其他變更執行適當的相關檢查；純文件修改執行文件與 diff 檢查即可。
- 推送後主動確認該提交的 GitHub Actions 結果；Python 3.11、3.12 與擴充套件等必要 jobs 全部成功，才可合併。失敗時優先修復，不在失敗基準上繼續堆疊無關變更；不得以跳過測試、弱化斷言或停用 CI 掩蓋問題。
- 部署前核對實際要部署的 commit SHA 與成功 CI 證據。合併產生新 SHA 時，等待該 SHA 的 CI 通過再部署；不可用舊提交或局部測試結果宣稱新版已驗證。部署後核對 revision、流量與服務回報的版本。
- 行為變更時同步檢查整合測試與 mock 契約。AI mock 依請求 schema 回傳對應格式，保留真實路由、failover、去重與無副作用等驗證；一般測試聚焦行為，精確文案只在文案契約測試中鎖定。
- CI 無法查證、測試未完成或環境阻擋時，明確記錄未驗證項目，不宣稱全綠、不合併或部署。取得可驗證證據後再繼續。
- GitHub 分支保護建議要求 PR 與必要 CI checks，禁止直接推送 main；本文件只規範代理行為，**不代表遠端已設定分支保護**。設定與實際狀態需另行查證，不能混稱已啟用。

## 歷史專案導覽（使用前核對目前程式）

# Chronos 專案速讀與維護備忘

核對日期：2026-10-14；程式基準：`7fe9eee`。以下為本機文件與原始碼確認結果，不代表 production 已部署或外部整合已驗證。

## 先讀

- [README](README.md)、[貢獻指南](CONTRIBUTING.md)、[文件入口](docs/README.md)。啟動、操作見 [getting-started](docs/getting-started.md)、[usage](docs/usage.md)。
- 時間與 AI 行為另看 [task-time-semantics](docs/task-time-semantics.md)、[gemini-failover](docs/gemini-failover.md)，並對照程式；舊文件有落差，見下方。
- [既有專案記憶](docs/project-memory.md) 記錄 TronClass 作業誤通知；本次保留原文，未實施修正。[TEST_REPORT](TEST_REPORT.md) 與 Study phase/evidence 文件是有日期的歷史紀錄。
- 本次開始時 repo／上層未找到適用的 `AGENTS.md`。此檔記錄既有實作與維護重點，不宣告新功能。

## 已確認架構與目錄

| 位置 | 責任 |
| --- | --- |
| `chronos/main.py` | FastAPI 入口、全域服務組裝、生命週期、Web API、Telegram webhook／指令分派、排程觸發 |
| `chronos/web.py` | Python 字串內嵌 HTML/CSS/原生 JavaScript；以 fetch 呼叫 `/api/tasks`，無獨立前端建置 |
| `chronos/tasks.py`、`task_timing.py` | 任務服務、確定性編輯、格式化、時間語意與期限排序 |
| `chronos/db.py`、`firestore_db.py` | SQLite／Firestore 兩套持久層；`create_database(settings)` 選擇後端 |
| `chronos/ai.py`、`gemini_keys.py`、`telegram.py` | Gemini 結構化輸出與金鑰切換、Telegram HTTP transport |
| `chronos/course_tracking*`、`study_scheduler.py`、`calendar_*` | 特定課表、課後進度狀態、校曆與通知 |
| `chronos/*bridge.py`、`*_sync.py`、`study_poll_*` | 瀏覽器觀察契約、資料匯入、持久化收集工作佇列 |
| `chronos/selection_*`、`pdf_*`、`summary_*`、`note_*` | 教材選擇、PDF 驗證、摘要工作、筆記保存與投遞 |
| `chronos/assignments.py`、`assignment_*`、`preparation_*` | 作業身分／截止／完成狀態、通知及草稿流程 |
| `chronos/gmail.py`、`mail_workflow.py`、`gmail_authorize.py` | 獨立 Gmail OAuth、郵件整理與 Telegram 卡片操作 |
| `chrome-extension/` | Manifest V3；原生 JavaScript、popup、content scripts、background worker，Node 內建測試 |
| `tests/`、`scripts/`、`.github/workflows/ci.yml` | pytest、示範／檢查／部署／現場驗證工具、CI |

這是單一使用者、自架的 Telegram 代辦服務，Web 為輔助介面；沒有多租戶帳號隔離。`#project` 只是任務標籤，不掃描 Git repository。Study 與 Gmail 預設關閉。

## 核心流程與不變條件

- **新增／編輯**：Web `POST /api/tasks/natural` 或 Telegram 自然語言 → `ExternalAI` → Pydantic 驗證 → `TaskService` → database。AI 失敗不新增／改動任務；`TaskService.parse()` 雖存在，主入口不將它當 AI 失敗備援。Telegram `/add` 原樣建任務、明確移除欄位／替換標題走確定性解析，不需 AI。
- **Telegram 一致性**：`prepare_message()` 先做外部解析，回傳同步 action；`process_update()` 在同一交易寫入 mutation 與 update receipt。投遞在交易後，逐則保存進度。保存的 edit retry 綁永久 task ID，並檢查解析期間是否有其他編輯。已送出訊息的 `edited_message` 不重播命令。送達後、寫 receipt 前中斷仍可能重複文字，不能承諾 exactly-once delivery。
- **ID／時間**：`/done 1`、`/edit 1` 的數字是當下未完成清單順位；`/edit #42` 是固定 ID，卡片回覆亦有訊息綁定。`due_at` 僅表示明確時間的期限；`timing.due_date` 為純日期期限，`scheduled`／`event` 分別為計畫工作／課程事件，`uncertain` 保留歧義。不補猜 09:00；排序只看期限，無期限最後、同期限依 ID。時間戳需時區；預設台北。
- **儲存**：SQLite 以 SQL 與 `initialize()` 的增量欄位更新管理 schema，無另設 migration framework；交易用 `BEGIN IMMEDIATE`／ContextVar。Firestore 使用 collection prefix 與交易，須先讀後寫；測試 fake 會拒絕 write 後 read。變更持久層需同步考量兩後端、舊資料相容與交易重試。
- **Study**：Chrome extension → loopback receiver `127.0.0.1:8765`（精確 extension Origin 與 bridge header）→ 本機 catalog／PDF → sync／worker／companion → 主資料庫與 Telegram。receiver 是獨立程序；開 Web 不等於 collector 或摘要 worker 已運作。NTOU／TronClass 課表、課程 ID 與部分 GCP 設定寫死，不能視為通用 LMS。
- **摘要／通知**：確認教材選擇後校驗 PDF hash、頁數與輸出 schema，先保存 canonical note，再標記 generation 完成與投遞。`SummaryJobs` 與 `StudyDeliveryLedger` 分開管理 claim；不確定結果標成 `uncertain`，不自動重試。Study 每次 AI 嘗試先預留每日 request/token 額度，預設兩者均為 0；它不是供應商帳單保證，也不是所有 task AI 的總額度限制。
- **Gmail**：使用本專案獨立 OAuth；可摘要、trash、archive、read 與經卡片確認新增 task，沒有寄信入口。郵件狀態以帳號 namespace 保存，remote writes 不放入資料庫交易。啟用會有真實郵件副作用，不能當純讀取示範。
- **排程／存取**：內建 APScheduler 每日代辦 08:00（設定時區），Gmail 07:00（固定台北），Study 啟用時每分鐘 tick。Cloud Run 部署腳本關閉內建 scheduler，建立外部 jobs；`-EnableGmail` 另加 07:00 mail job。Web 密碼空值時無 Basic Auth；webhook secret、chat ID 只有設定後才檢查；internal endpoints 要求 scheduler secret。`/health` 僅回傳 ok，非依賴健康檢查。

## 依賴與常用指令

`pyproject.toml`：Python >=3.11、Hatchling、版本 0.1.0；FastAPI、Uvicorn、HTTPX、pydantic-settings、APScheduler 3.x、google-cloud-firestore、pypdf、tzdata。`dev` 加 pytest／pytest-asyncio；`collector` 加固定版 Playwright 1.63.0。未找到納入 Git 的 lockfile、Ruff／Black／mypy 設定。CI 使用 Python 3.11／3.12、Node 22；Docker 使用 Python 3.12。

於 repo 根目錄（PowerShell）：

```powershell
# 首次安裝；不要覆寫既有 .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
if (!(Test-Path .env)) { Copy-Item .env.example .env }
# 一般啟動：會載入既有設定，可能啟動排程／註冊 webhook
.\.venv\Scripts\python.exe -m uvicorn chronos.main:app --host 127.0.0.1 --port 8000
# 檢查
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts/check_docs.py
.\.venv\Scripts\python.exe scripts/check_demo.py
node --test chrome-extension/*.test.cjs chrome-extension/*.test.js chrome-extension/test/*.test.js
git diff --check
```

macOS/Linux 改用 `.venv/bin/python`。測試依模組選跑；外部服務多用 AsyncMock／HTTP transport mock／Firestore fake，不能視為 live integration。文件檢查只驗本地檔案連結，不驗網址或 heading anchors。Docker Compose 綁定 loopback 8000，SQLite 在 named volume；`down -v` 會刪資料。

設定來源為 `chronos/settings.py` 的 `CHRONOS_` 環境變數及 `.env`，空環境值被忽略。`settings`／`main` 在 import 時建立設定與服務；測試隔離需在 import 前處理設定，不能僅靠事後換 db。不要輸出／提交 `.env`、`.gmail/`、token、cookies、個人 DB 或教材；避免執行 `scripts/verify_*`、部署、session publisher 當成無副作用的檢查。PR 說明分開列本機、mock 與 live 證據；本地沒有 LICENSE 檔，依 CONTRIBUTING 先釐清授權再作程式碼貢獻。

## 已確認落差與尚未確認

- **文件落差**：README 的 Gmail 08:00 已落後 `main.py`／部署腳本／`docs/gmail.md` 的 07:00；usage/getting-started 的「原始輸入不保存」不適用於目前 `timing.source_text`，含時間資訊的新增會保存原始輸入（最多 2000 字元）。舊文件的完整年份顯示、`/tasks` Edit 按鈕、429 即時重試描述亦須以現行程式核對：目前日期顯示省年份，`/tasks` 本身只回文字，429 進入 cooldown。
- **已確認作業風險**：`assignment_bridge.py` 僅接受 submission `submitted/unknown`；`assignment_sync.py` 都可匯入；`assignment_scheduler.py` 對未結案記錄可發首次通知。來源已繳交不等於本地完成，故仍可能發「新作業」。正確性修正尚未進行；真實誤通知個案的頁面、課程、source ID 與部署版本尚未查證。
- **未確認**：目前 production revision、Cloud Scheduler 實際狀態、Gemini 模型／配額可用性、真實 Gmail／Telegram／TronClass 登入與端到端投遞。本次未讀取私密設定或呼叫這些服務。
- **本次檢查**（本機 Python 3.12.14／Node 24.14.0）：Chrome extension 48 項通過；文件檢查 31 份 Markdown、67 個本地連結、0 缺失。指定 Python 測試 `test_tasks`／`test_assignment_bridge`／`test_assignment_sync`／`test_assignment_scheduler` 以 `-x` 執行，28 passed 後 HTTP handoff 測試因 loopback socket `WinError 10013` 失敗，後續未跑完。預設暫存位置曾遇權限錯誤，改用 `--basetemp=.pytest-tmp/<新的專用目錄>`；包含 `test_task_timing` 的另一輪未完成而中止。Demo 首次遇暫存權限錯誤，改 TEMP/TMP 到 repo 後仍於 60 秒逾時，原因未定位。未執行全套 Python 測試，不能宣稱全綠；本次只新增摘要，未修程式或部署。


## Chronos reply style (2026-10-23)

- Apply the existing secretary voice to every new deterministic template, including clarification and review flows. Follow docs/reply-style.md: readable local time ranges, separate candidate lines, one necessary grouped question, brief before/after blocks, and no normal internal event IDs or raw ISO timestamps. Add output-contract tests; a prompt or memory preference alone does not enforce hard-coded replies.

- Use restrained, formal, courteous English with a British butler sensibility. Address the user as Sir in acknowledgements and introductions, sparingly. Be calm, concise and precise; avoid emoji, exaggerated praise and repetitive service catchphrases.
- Dry wit is optional and rare, suitable only for harmless situations. Never joke about failures, missed deadlines, uncertainty or destructive confirmations; never mock the user.
- Anticipate useful next steps only from verified information and actual capabilities. A proposal is not a completed action. Preserve explicit approval, uncertain delivery and no-change wording.
- Prefer one compact acknowledgement per request. Batch creation groups task details and retains stable-ID edit buttons; individual approvals remain separate. Do not bind a multi-task card to one arbitrary task.
- Use chronos/reply_voice.py for task acknowledgement presentation; update other owned templates at their source. Never rewrite task titles, quoted mail, study notes or user content to add personality. New user-facing templates must follow this policy, with output-contract tests where relevant.
- Natural interaction is the goal. Never claim to be a human, conceal assistant identity when asked, or invent emotions, background activity or completed work.


## Default planning acceptance (2026-10-24)

- The owner authorizes routine task work-session scheduling without per-session proposals or Accept buttons. Default CHRONOS_LEARNING_AUTO_ACCEPT=true; explicit manual mode remains supported.
- Verify current task snapshot, Calendar availability, deadline, working hours and daily workload before booking. Reuse deterministic event reconciliation and report only verified bookings.
- Moving other events or exceeding working hours remains an explicit exception; do not silently change deadlines. No-slot and recovery decisions stay visible.
- This standing booking authorization does not authorize application deployment or arbitrary external Calendar modifications.
