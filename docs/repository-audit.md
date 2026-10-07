# Chronos repository 審查與改善方案

審查日：2026-10-07（Asia/Taipei）。來源為本機程式、設定、測試、工程紀錄，以及 GitHub API 的即時資料。本報告中的推廣效果都是待驗證假設，沒有宣稱已提升曝光或使用量。

## 現況診斷

核心用途是 single-user Telegram task assistant：以自然語言新增或編輯個人代辦，提供清單、完成操作、每日未完成摘要及輔助 Web。Gemini 負責自然語言解析，SQLite / Firestore 負責持久化；不是全離線工具，也沒有多使用者帳號隔離。Study 擴充包含課後進度、教材、作業、筆記與 NTOU TronClass bridge，但目前存在固定課表、課程 ID 與 GCP project，不能描述為通用學生平台。

首要使用者是會自行部署、習慣 Telegram 的個人使用者。對特定 NTOU 課程的學生或維護者而言，Study 有研究價值，但 onboarding 成本高，不能把這個族群與一般代辦使用者混成同一條快速安裝主線。

一句話主張：**把中英文訊息變成代辦，在自己的 Telegram bot 管理期限與完成狀態，每天收到未完成清單。**

主要障礙是「看不出適合誰 → 不確定能否使用 → 不知道如何體驗」，不是缺少更多功能宣稱。Chronos 名稱通用，空白 About 缺少搜尋語意；長 README 把進階交易細節、專用 production 與入門混在一起；缺少可看的結果與授權資訊，訪客難以判斷投入設定是否值得。

## 查核清單與證據

| 項目 | 審查前的實際狀態 | 判斷與本次處理 |
| --- | --- | --- |
| README | 繁中、長篇操作及交易細節，功能與 Study 範圍不易掃讀 | 加上具體定位、英文摘要、條件表、Demo / setup / usage 入口。 |
| GitHub description / topics / homepage | API 回傳 `null` / `[]` / `null` | 下方準備精確設定值；尚未寫入遠端 About。 |
| 檔案結構 | 程式與測試分離；大量帶 phase 的工程文件 | 不重排程式，新增 docs index，區分使用入口與歷史證據。 |
| 安裝 | 有 Windows pip 與 Docker 指令；缺 clone、可期待結果及排錯 | 補 Windows / POSIX、健康檢查、第一筆代辦、Gemini 前提。 |
| 使用例 | 例子存在，但位置 ID、Study ID 與 AI 條件難懂 | 首頁只放一條完整代辦流程，細節移至 usage。 |
| 文件可信度 | Extension README 聲稱不送網路、不讀 cookies；目前程式已不同 | 依 manifest / popup / session-import / publisher 修正，列出固定綁定。 |
| 圖片 / Demo | Git tracked 檔案沒有產品截圖；production 需要私有登入 | 新增隔離本機示範，已擷取實際 Web 合成資料畫面；私有 instance 不當公共 Demo。 |
| Release | GitHub releases API 回傳空陣列；package version 為 0.1.0 | 不把 package version 等同正式 Release；待授權與驗收後發布。 |
| License | 無 License 檔，GitHub `license: null` | 明示缺口，未代替擁有者選授權，也未添加 open-source 宣稱。 |
| Contribution | 無 CONTRIBUTING、Issue / PR templates | 新增具體回報方式與模板；程式碼貢獻先釐清授權。 |
| CI / 測試 | Python 3.11 / 3.12 workflow；最新主線 CI 成功 | 加真實 workflow badge；本機 extension 已通過 48 項測試。 |
| 搜尋語彙 | 名稱與中文敘述為主；package 仍寫 project assistant | 英文摘要與 package metadata 對齊 Telegram / task / self-hosted。 |
| 活動基線 | Stars 0、Forks 0、open issues 0；Discussions 未開 | 不捏造人氣、不灌 Issue；Traffic 與實際使用者未取得。 |

遠端證據：[repository metadata](https://api.github.com/repos/Yili-code/Chronos)、[releases](https://api.github.com/repos/Yili-code/Chronos/releases)、[最新已完成 CI](https://github.com/Yili-code/Chronos/actions/runs/37556404015)。數值是審查快照，不是長期指標。舊 TEST_REPORT 的 production 與 pending release 不能合併為「目前都已上線」。

## 依影響與修改成本排序

影響 1–5（高越重要），成本 1–5（高越費時），優先效率＝影響／成本。這是決策估計，不是流量預測；同分時先處理信任與採用阻礙。

| 順序 | 修改 | 影響 / 成本 / 效率 | 解決問題與可能增加觸及的原因 | 如何驗證 | 狀態 |
| --- | --- | --- | --- | --- | --- |
| 1 | 修正 extension 資料流及 Study 邊界 | 5 / 1 / 5.0 | 消除權限敘述矛盾，降低試用後失信；正確定位有助合適使用者分享 | 對照 manifest 與資料路徑；請新訪客復述哪些資料會外送 | 本機文件已修改 |
| 2 | 決定 License | 5 / 1 / 5.0 | 授權不明阻礙採用、整合與外部貢獻；消除合法使用疑慮可增加引用 | License 檔 / GitHub 辨識；記錄授權相關提問是否減少 | 擁有者待決 |
| 3 | 填 About description / topics | 4 / 1 / 4.0 | 提供 Telegram task bot 的搜尋與分類語意，補足通用名稱 | 搜尋完整名稱與相關詞、Traffic referrals 與訪客；不保證排名 | 值已準備，遠端未改 |
| 4 | README 首屏與條件表、文件分流 | 4 / 1 / 4.0 | 快速說明適合誰、需要什麼；訪客與分享者較能正確描述用途 | 找 3–5 位目標訪客讀首屏，記錄理解與點擊下一步；人數為招募目標 | 本機已修改 |
| 5 | CONTRIBUTING 與 Issue / PR 模板 | 3 / 1 / 3.0 | 降低回報成本，讓有用回饋成為公開可搜尋解答 | Issue 是否有完整重現步驟、首次回應時間、有效解決比例 | 本機已修改 |
| 6 | 指令與 setup 的成功條件、排錯 | 5 / 2 / 2.5 | 安裝可成功才有自然口碑，避免以為無 key 就能新增 | 乾淨環境首次啟動與第一筆任務；記錄阻塞率和完成時間 | 文件已改；跨平台 / live 待驗 |
| 7 | 隔離 Web demo、真實畫面素材 | 4 / 2 / 2.0 | 在 key / bot 設定前看到實際清單，增加嘗試意願與可分享性 | Demo 能顯示、完成合成任務；向受試者確認 demo 範圍 | 本機 demo、截圖與瀏覽器檢查完成 |
| 8 | 首個可重現 pre-release | 4 / 3 / 1.3 | 提供可引用版本、已知限制與更新入口，建立維護可信度 | 在新環境從該 tag 安裝；發布 notes 與檢查結果一致 | 計畫，未發布 |
| 9 | Telegram 短錄影與可使用案例 | 4 / 3 / 1.3 | 展示主介面的實際結果，訪客可在社群轉貼操作片段 | 遮蔽私密資料後，驗證新增→編輯→完成；按渠道量測點擊 | 待素材 / live 驗證 |
| 10 | 英文完整入門與英文技術分享 | 3 / 3 / 1.0 | 英文摘要可先被辨識，完整入門則減少國際訪客的採用阻礙 | 國際訪客按英文指令完成流程；比較有效試用數 | 英文入門已完成，外部文章未發布 |

優先效率是影響乘以成本的倒數；同分先處理信任障礙。安裝成功仍是公開發佈與社群推廣的必要前提，不能只因 About 修改便宜就先擴大宣傳。

## 首頁與可發現性設定

首頁架構已改為：定位 → 適用者及開發 / 授權狀態 → 功能條件 → 實際輸入流程 → 快速啟動 → 文件 / 證據 → 回饋入口。低層交易與部署細節移到對應文件，保留對失敗行為的精確描述。

建議 GitHub About description（可直接使用）：

> Self-hosted, single-user Telegram task assistant with Gemini-powered natural-language input, a Web dashboard, and a daily task digest.

建議 topics：`telegram-bot`, `todo`, `task-management`, `self-hosted`, `python`, `fastapi`, `gemini-api`, `sqlite`, `firestore`。沒有通用 LMS 支援、全離線模型或多使用者服務，不加入這類詞。`study-assistant` 只在主張明確限定 Study 範圍後考慮；不要為搜尋量加入不相關熱門詞。[GitHub topics 文件](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) 說明 topics 作為分類及探索入口，不代表有搜尋排名保證。

Homepage 在沒有公開網站時可先留空；有已發布的使用文件或隔離 Demo 後才設定該 URL。GitHub README 沒有自訂 meta tags / analytics 的能力；關鍵字應自然放在標題、摘要、使用例與圖片 alt，無須堆字、空 Wiki、重複翻譯頁或占位 landing page。後續獨立文件站應在內容足夠時才做，避免再多一個需維護的入口。以包含功能詞的文章與相關來源連結測試 discoverability；Search Console 只用於自己可驗證的網站，不能直接代表 GitHub 的搜尋曝光。

## 同類專案：借鑑的是資訊設計

| 專案 | 實際觀察 | 適用 Chronos 的借鑑與驗證 |
| --- | --- | --- |
| [tududi](https://github.com/chrisvel/tududi) | 同類 self-hosted task manager，有 Telegram integration，提供截圖及清楚的功能分類 | 用真實清單畫面降低理解成本；以讀者能否指出「輸入、期限、完成」驗證，不照搬其多功能版圖或品牌。 |
| [Vikunja](https://github.com/go-vikunja/vikunja) | 同類 task manager，README 導向試用、安裝、開發、授權與安全通報 | 給一般使用者和貢獻者不同入口；追蹤 first-run 成功率與回報品質，不借用其標語或 hosted 宣稱。 |
| [calibot](https://github.com/mallahyari/calibot) | 鄰近 Telegram natural-language assistant，README 有錄影、自然語言例子及前提；安裝仍出現 yourusername placeholder | 借鑑操作錄影，不移植其 OAuth / Calendar 功能；Chronos 使用真實 clone URL，逐步验证输入例。 |
| [Karakeep](https://github.com/karakeep-app/karakeep) | 鄰近 self-hosted AI 工具，安裝、配置、截圖、安全文件分開；公共 Demo 使用預填且 read-only 資料 | 如將來做 hosted Demo，可採隔離合成資料與受限操作；目前只做本機示範，量測看過示範後是否成功啟動。 |

這些專案規模、社群與功能不同，不能將其 stars 視為首頁設計帶來的因果證據。

## Demo、案例與參與轉換

現有本機 Web demo 使用真實介面、暫存 SQLite 與兩筆合成資料，不能當作 AI 解析或 Telegram 投遞證據。下一份素材以自己的測試 bot 錄製 30–60 秒「報告代辦 → 查詢 → 移除期限 → 完成」，另一段展示一次編輯失敗後的 Retry / Cancel；只在實際測試成功後使用。這解決主介面看不到結果的問題，可能增加分享與試用；用影片至 setup 的訪問與第一筆任務完成率驗證。

Study 案例用自己有權分享的 PDF 或課程資料，明示選取、摘要、人工確認與匯出邊界。若資料使用權或跨校安裝尚未驗證，先保留在工程文件。社群宣傳不得使用模擬 Telegram 截圖假冒真實操作。

轉換路徑：理解用途 → 本機 Demo → 安裝 → 成功建立與完成一筆任務 → 回報實際阻礙 / 分享情境 → 自願 Star 或關注 Releases。Issues 用來解決問題，Stars 用來收藏，沒有投票換功能、抽獎或互刷。先維持 Issues 單一入口；真有重複討論需求再開 Discussions，以回覆負擔及有效討論數驗證是否值得。

## 適合的發布與分享渠道（未發布）

先選與已驗證用途相關的渠道；沒有排定推廣日程，也沒有建立提醒或自動化。

| 渠道 | 解決問題與可能提升觸及的原因 | 前提與成效驗證 |
| --- | --- | --- |
| GitHub pre-release | 固定版本、已知限制與更新入口增加引用及採用信任 | 授權釐清，從 tag 可重現安裝；看新版本安裝成功率與回報。 |
| 自己既有的 Telegram / Python / 台灣技術社群 | 語言與操作情境接近目標使用者，利於具體回饋與自然分享 | 允許作品分享，提供真實操作；記錄有效試用與安裝卡點，不重複洗版。 |
| 個人技術部落格 / Dev.to | Telegram task assistant、Gemini、self-hosted 長尾詞可形成較有用的搜尋內容 | 發可重現部署案例；比較文章至 repository 的抵達與成功試用。 |
| r/selfhosted / TelegramBots | 自行部署與 Telegram 使用者可能與定位相符 | 發文前重查版規，英文 walkthrough 完成後再考慮；看實際試用與技術提問。 |
| Show HN | 面向願意動手試玩的使用者，利於檢驗定位 | 先有容易體驗的成果；看有效試用而非只有 stars。[官方指南](https://news.ycombinator.com/showhn.html)。 |
| awesome-selfhosted | 策展入口可能帶來較相關的長期流量 | 有明確授權、維護證據並符合當時收錄規則後再考慮；目前不宣稱符合。 |

Product Hunt、付費廣告與建立大量新社群目前不是優先項，因為尚未驗證低摩擦試用與目標受眾；若未來採用，以有效試用成本與維護負擔驗證價值。

## 指標：來源、公式與限制

| 指標 | 量測方式與用途 | 限制 / 決策 |
| --- | --- | --- |
| Unique visitors / views | GitHub Insights → Traffic；每週保存 14 天視窗，以發布日期標記變更 | 不直接當成 README impression；不加總重疊視窗的 unique visitors。 |
| Clones / unique cloners | 同一 Traffic 視窗；發布前後按日比較 | 完整 clone 不是 fetch；clone 可能來自自動工具，也不是安裝成功。 |
| README → Demo | GitHub 原生沒有 outbound click analytics。未來 Demo 網站以 `utm_source=github&utm_medium=readme&utm_campaign=...` 記錄抵達 | 可量到 tagged arrivals；無 README impression 分母時只能報抵達數或每 repo view 抵達的粗略比值，不能標為真正 CTR。現在本機示範無點擊數。 |
| Stars / Watch / forks | 每週保存累積與新增；對照文章或 Release 日期 | 收藏 / 訂閱是興趣訊號，不是活躍使用者；不用小樣本比例宣稱成功。 |
| Issues 品質 | 記錄安裝問題數、可重現比例、首次回覆時間、解決率 | Issue 多可能代表採用增加或缺陷增加，需分類解讀。 |
| First-run 成功率 | 自願試用者中，成功建立並完成第一筆任務 / 實際開始安裝者 | 明確人數與時間，避免只詢問成功者；不是所有 clone 的轉換率。 |
| 實際使用者 / 7 日留存 | 同意回覆的試用者自報使用及次週使用；先用人工、匿名彙總 | 自行部署無中央 telemetry，維護者 bot 人數不代表所有部署。未回覆者列未知。 |
| 搜尋來源 | Traffic referring sites / 自有網站 Search Console；記錄可見搜尋入口與關鍵詞 | GitHub 不提供完整 query / impression，無法推算精確搜尋 CTR。 |

[GitHub Traffic 文件](https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository) 說明可查看最近 14 天訪客、完整 clone 與來源；需 push 權限。[Traffic API](https://docs.github.com/en/rest/metrics/traffic) 可作後續定期保存。工具目前沒有取得 Traffic 數值，因此本報告不填零或推估值。匿名彙總足以初期決策，不需加入產品 tracking。

## 尚需決定或提供

1. License 的授權政策與著作權資訊；這是擁有者決策，本次未替你套用授權。
2. 主定位已採一般個人 Telegram 代辦，Study 作進階專用模組；這項文件資訊架構已自主完成，不需要你另做決定。
3. 自己測試 bot 的真實錄影、可公開的 Study 素材與資料使用權；Web 合成示範不替代這些證據。
4. 是否經營獨立公開 Demo / 文件站及承擔其維運；目前可先用本機 Demo。
5. Review 後提交 / 發布文件、GitHub About 設定與首個 pre-release；本次只有本機檔案變更，未 push、發 Release 或發社群訊息。
6. Traffic 基線與自願試用者招募；未取得的數字保持未知。

## 本次驗證結果

已驗證完成：

- 在新 `.venv` 使用 Python 3.12.14 執行 `pip install -e ".[dev]"`，安裝成功；`pip check` 無相依衝突。這台電腦預設 Python 3.13 的 venv 建立有環境問題，因此使用可用的 3.12 runtime；不是 3.13 安裝成功的證據。
- 全部 Python 測試 `621 passed`，耗時 91.39 秒；有一項 Starlette / httpx deprecation warning。這是本機測試，不代表最新程式已部署。
- Chrome extension 的全部 Node 測試 `48 passed`，沒有失敗。
- 真實瀏覽器開啟 local demo，確認 `/health` 成功、兩筆合成任務顯示、按 Complete 完成兩筆、空清單顯示正確、自然語言新增在無 Gemini key 時回傳 503 且資料未寫入。頁面無 JavaScript error，390px viewport 沒有橫向溢出。
- 以文件的 `python -m uvicorn chronos.main:app` 啟動實際應用，使用獨立空白 SQLite；Web、`/health` 與 `/api/tasks` 均成功，未設定外部服務。
- 擷取並人工檢視 `docs/images/web-demo.png`，為實際程式介面與合成任務，不是 AI 生成 mockup。
- `local_observation_server --help` 與 `run_summary_companion --check` 可執行；後者明確回傳 `remote_readiness: not_checked`。
- 45 個 repository Markdown 相對檔案連結有效；新增 / 修改文件的 code fences、TOML、Issue forms YAML 與 Compose 靜態結構通過。11 個文件 PowerShell code blocks 與部署腳本語法通過，`git diff --check` 通過。POSIX 指令僅依結構與路徑審查，沒有宣稱跨平台實機通過。
- 主要外部文件連結可由官方來源讀取；AI Studio API key 頁會要求登入。自己的 GitHub metadata / releases / CI 由 connector 讀回，web crawler 的 cache miss 不視為 repository 不存在。

尚未驗證：macOS / Linux 實機、Docker build / run（此環境未安裝 Docker）、新的 live Gemini / Telegram 操作、真實 TronClass session / PDF、Cloud Run 重新部署，以及宣傳的流量效果。文件保留了條件與限制；沒有將這些寫成成果。

實際修改為 README、啟動 / 指令 / 部署 / Demo / 文件入口、Chrome extension README、CONTRIBUTING、Issue / PR 模板、package 描述及來源連結，以及額外的隔離 demo launcher 與實際截圖。`chronos/` 的核心應用程式未修改，GitHub About / Releases / 社群也未寫入或發布。

## 自主優化補充

- 已新增 [英文入門](../README.en.md)，涵蓋兩種作業系統的安裝、Demo、Gemini、Telegram、操作範例、資料流與排錯。這解決英文訪客只能看摘要的障礙；後續以首次使用成功率驗證採用成效，不宣稱已增加流量。
- 已將 Chrome extension 測試、文件連結檢查及 Demo 隔離驗證加入 CI 設定。這讓可見文件與試用路徑在後續修改時持續受檢查，降低失信與安裝失敗；以 workflow 執行結果驗證。新 workflow 尚未推送，不能描述為 GitHub 已通過。
- `scripts/check_docs.py` 已驗證 26 份 Markdown 的 57 個本機檔案連結，沒有缺失；不連網、不檢查 heading anchors。
- `scripts/check_demo.py` 已驗證既有 `.env` 與環境中的合成憑證不會讓 Demo 連上 Telegram、Gemini、Firestore 或啟用排程；任務顯示與完成、AI 失敗不寫入、既有資料不變及正常結束後暫存資料庫清除均通過。此檢查以 TestClient 執行，不替代之前完成的瀏覽器畫面驗證。
- GitHub About 修改已嘗試查核入口；可用瀏覽器未登入，現有 connector 沒有 description / topics 寫入工具，故尚未套用。前面準備的設定值仍可直接使用；沒有修改帳號、權限或建立憑證。
- 本輪檢查期間另有核心程式變更出現；本輪只編輯文件、CI 與檢查工具，保留其他修改，未提交或推送混合變更。前述 621 項完整測試屬上一輪快照，不代表這些同期程式變更也已通過。
