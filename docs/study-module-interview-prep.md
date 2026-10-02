# Chronos Study Module：Phase 0 面試準備文件

這份文件把 Chronos Study Module 的 Phase 0 feasibility spike 整理成面試可口述的版本，並說明它如何銜接 Phase 1 的 course tracking。內容只使用目前 repository 中已完成的程式、測試與實際觀察；尚未驗證的部分會明確標出。

## 0. 先建立系統直覺

Chronos 不直接接管學校帳號，也不把密碼或 browser cookie 搬到自己的程式。使用者在 Chrome 完成海大 CAS login，Chrome extension 只回傳經過 redaction 的頁面觀察，例如目前是否仍在 TronClass、是否被導向 CAS login；local Chronos process 再依這個觀察做 read-only 的資料分類。

流程可以濃縮成：

```text
YiLi 在 Chrome 登入 CAS
        ↓
TronClass authenticated page
        ↓ 只傳安全觀察，不傳 cookie / token / password
Chrome extension → loopback local bridge → browser-session adapter
        ↓
OK / REAUTH_REQUIRED / UNKNOWN / DEFERRED_ATTACHMENT
```

Phase 0 的核心不是「做出完整爬蟲」，而是回答 feasibility questions：REST CAS login 是否可靠、TronClass 的資料是否可讀、PDF 是否能穩定保存、session 的生命週期能否被安全地理解，以及官方校曆是否能 fail closed 地解析。

### 已驗證的事實

- REST CAS probe 兩次都拿到 CAS TGT 與 service ticket，但 ticket-to-TronClass session exchange 最後回到 CAS login；所以 REST login 在目前證據下不可靠。
- 使用者在瀏覽器登入後，可以讀到課程、公告、作業、deadline 與附件；這是 browser-mediated access，不等於 REST adapter 已可用。
- 同一個 PDF 在兩個 browser profiles 共下載 5 次，檔案大小都是 466,300 bytes、14 pages、PDF 1.7，SHA-256 都是 `fd31bde477c7c1e3b00e56139f1e37019b542268e7bb51fa9e4e1b888f1d3a14`。
- 對同一個已登入 browser session 做 reopen/reload，30 秒與 60 秒內仍停留在 authenticated dashboard；精確 cookie、CSRF token 與 expiry 並未讀取，因為它們屬於 credential material。
- CAS redirect、timeout、unknown page 都會被分類，不會把 `HTTP 200` 直接當成 authenticated。
- 目前 focused tests 為 Python 29 tests 加 extension 3 tests；full pytest 仍受環境缺少 `fastapi`、`apscheduler` 影響，不能宣稱全套測試通過。

### 尚未驗證的事實

- 其他 PDF、非 PDF 附件、較大檔案與長時間 session expiry。
- Chrome extension 在所有瀏覽器版本上的長期穩定性。
- Phase 1 的真實 Telegram/Firestore production delivery。

## 1. 面試問題與參考回答

以下每題都用「直覺 → 原理 → 實作」的順序準備。回答可直接口述，再依追問補充。

### A. Core concepts

#### A1. CAS 是什麼？為什麼需要它？

**面試官想評估：** 是否理解 centralized authentication 與 service session 的差異。

**參考回答：** CAS（Central Authentication Service）是一個集中式登入系統。學校讓使用者只在 CAS 輸入一次帳密，其他校務服務透過 service ticket 確認「這個人已經被 CAS 驗證」，而不必每個服務各自保存密碼。這能減少密碼散落、統一 logout 與帳號政策；但 CAS ticket 被兌換成應用程式 session 後，兩者不能混為一談。

**核心原理：** authentication（確認身份）與 session management（維持已登入狀態）是兩個階段；ticket 通常是短期、一次性 credential。

**追問方向：**

- 為什麼拿到 service ticket 還可能未登入 TronClass？因為 TronClass 還需要正確的 callback、cookie、redirect 與 session exchange。
- CAS password 應不應放進 extension？不應；讓瀏覽器承擔登入邊界，local process 只接收 redacted observation。

**常見誤解：** 「拿到 TGT 或 service ticket 就代表所有服務都已 authenticated。」

#### A2. `HTTP 200` 能證明登入成功嗎？

**評估：** 能否用 semantic evidence 而不是單一 status code 判斷狀態。

**參考回答：** 不能。登入失敗頁、CAS login form、錯誤頁都可能回 200。這次 probe 的兩個 REST attempt 都是 200，但 final origin path 回到 CAS login，因此應分類為 `REAUTH_REQUIRED` 或 `tronclass_session` failure，而不是 OK。

**原理：** transport success 不等於 application success；需要 URL、頁面 marker、資料結構與 expected identity 的多個訊號。

**追問：** 若頁面 marker 改名怎麼辦？回 `UNKNOWN`，通知 adapter maintenance，不能誤判成沒有課程。

**誤解：** 只檢查 status code，或只檢查 response body 非空。

#### A3. CSRF token 是什麼？

**評估：** 是否理解 web security 的 threat model。

**參考回答：** CSRF（Cross-Site Request Forgery）是攻擊者利用使用者已登入的 cookie，誘導瀏覽器對目標網站送出非本人意圖的 request。CSRF token 是網站放在頁面或表單中的不可預測值，server 會要求 request 同時帶上正確 token，降低第三方網站偽造操作的可能。Phase 0 只做 read-only verification，因此不應自行提交作業，也不應為了探測而擷取或記錄 token。

**原理：** browser automatically sends cookies；token 提供另一個 attacker 不容易取得的 request-bound secret。

**追問：** GET 是否一定安全？不一定；工程上仍應避免讓 GET 改變狀態。

**誤解：** 把 CSRF token 當成密碼，或認為有 cookie 就不需要 CSRF protection。

#### A4. Browser session 與 REST session 有什麼差別？

**評估：** 是否能選擇正確的 integration boundary。

**參考回答：** Browser session 由真實 Chrome 維持 redirect、cookie、頁面 JavaScript 與 CAS 流程；REST session 則由程式自己重現這些細節。這次 REST route 不穩定，但 browser-mediated login 與 read-only observation 可行，所以 adapter 應以 browser session 為 authentication boundary，而不是急著複製 login protocol。

**原理：** implementation coupling 與 protocol completeness；少讀 secrets 也降低 security exposure。

**誤解：** 「REST 比瀏覽器一定更可靠」或「只要模擬幾個 HTTP request 就等價」。

### B. Implementation details

#### B1. Chrome connector 實際傳什麼？

**評估：** 能否設計 narrow interface 與 data minimization。

**參考回答：** connector 只需要 `current_url()` 與經過限制的 `visible_text()`，再產生 `AUTHENTICATED_PAGE`、`CAS_REDIRECT`、`TIMEOUT` 或 `UNKNOWN_PAGE`。它不讀 cookie、local storage、authorization header、CSRF token、password 或完整 response body。

**原理：** least privilege、capability boundary、redaction before persistence。

**追問：** URL query 怎麼處理？只保留 origin/path，丟掉 query、fragment 與 userinfo。

**誤解：** 把整頁 HTML 或 network log 送回 local service。

#### B2. 為什麼要有 `ReadResult`？

**評估：** 能否把資料與失敗語意一起建模。

**參考回答：** `ReadResult[T]` 把 `status`、安全的 `value` 與 `SafeDiagnostic` 綁在一起。這讓呼叫端知道「有資料」「需要重新登入」「暫時未知」或「附件保存被延後」，而不是用 `None` 混淆所有失敗。對 production system，這直接影響 retry、通知與是否可以繼續處理。

**原理：** explicit state machine 與 typed result；fail closed。

**誤解：** 遇到例外就回空 list，讓上層誤以為真的沒有資料。

#### B3. `READY → REAUTH_REQUIRED → UNKNOWN` 如何發生？

**評估：** 是否理解 state transition 與不確定性。

**參考回答：** authenticated marker 存在時是 `READY`；觀察到 CAS login path 時是 `REAUTH_REQUIRED`；transport timeout 或頁面不符合任何安全 marker 時是 `UNKNOWN`。`UNKNOWN` 不是 logout 的同義詞，而是 evidence 不足，所以可以有限 retry 或請使用者重新開啟頁面。

**原理：** observable state、closed-world classification、uncertainty preservation。

**誤解：** 所有非 READY 狀態都自動重登，或把 UNKNOWN 當成空資料。

#### B4. PDF repeated-download 怎麼驗證？

**評估：** 是否會做 reproducible integrity check。

**參考回答：** 我們固定同一個 activity 與附件，在兩個 browser profiles 重複下載，記錄 sample count、HTTP-independent file properties、SHA-256 與 `pdfinfo`。五份 sample 都是 valid PDF、14 pages、466,300 bytes，checksum 相同，所以「這個已測附件」的 repeated-download gate 通過；但不能推論所有附件都通過。

**原理：** content integrity、cryptographic fingerprint、scope of evidence。

**誤解：** 只看檔名存在，或只下載一次就宣稱穩定。

### C. Design decisions

#### C1. 為什麼選 browser-mediated connector，而不是直接重做 CAS REST client？

**評估：** 能否用 evidence 做 architecture trade-off。

**參考回答：** REST probe 取得過 ticket，但兩次 session exchange 都回 CAS login；browser flow 則可讀取已登入內容。直接重做 CAS client 需要處理 redirect、cookie、ticket、CSRF、JavaScript 與頁面變更，且會擴大 credential exposure。因此 Phase 0 的結論是先採 browser-session adapter，並保留 REST route 為 deferred research，而不是把不穩定的流程包裝成成功。

**原理：** reliability、security exposure、maintenance cost 的 trade-off。

**誤解：** 只因 REST 較容易測試就選它，忽略實際失敗證據。

#### C2. 為什麼附件失敗要回 `DEFERRED_ATTACHMENT`？

**評估：** 是否能設計安全的 partial success。

**參考回答：** metadata 能列出不代表 bytes 已可靠保存。若 repeated-download 或 persistence gate 沒通過，系統應保留「看得到附件、但不能承諾已保存」的語意，讓使用者回瀏覽器處理，而不是回傳損壞檔或靜默遺失。

**原理：** explicit degradation、data integrity、fail closed。

**誤解：** 下載失敗就忽略附件，或標成成功讓後續 summary 使用不完整資料。

#### C3. 為什麼 diagnostics 不放完整 URL？

**評估：** security-aware observability。

**參考回答：** CAS service ticket 可能出現在 query string，cookie 與 header 也可能直接等同 credential。diagnostic 只保留 operation、retryable 與有限狀態，不保留 URL query、token、cookie、password 或 response body。這會降低除錯細節，但保護性遠高於把 secret 寫進 log。

### D. Debugging scenarios

#### D1. 顯示 200 但仍回 CAS，怎麼查？

**回答：** 先比對 safe origin/path 與 redacted page classification；確認是否有 authenticated marker。再檢查 redirect chain 的每個 stage 是否為預期，不重播或輸出 ticket。最後把結果分類成 `REAUTH_REQUIRED`，建立 fixture test，避免把錯誤當成 empty course list。

**追問方向：** 可能是 callback mismatch、session cookie 沒被保留、service ticket 已使用或頁面 flow 改變。

#### D2. PDF 第一次成功，第二次變成 HTML login page，怎麼處理？

**回答：** 先做 magic bytes、EOF、page count、size 與 checksum 檢查；不以檔名或 200 判定成功。第二次 sample 失敗就停止宣稱 persistence，回 `DEFERRED_ATTACHMENT`，保留安全 failure reason，要求重新驗證 browser session。

#### D3. 頁面文字改了，authenticated marker 找不到，怎麼辦？

**回答：** 不把它視為 logout；回 `UNKNOWN`，保留 operation 與 retryable 資訊，增加新的 fixture 後再更新 marker。這是 adapter maintenance signal。

### E. Performance and security

#### E1. 這個設計的效能瓶頸在哪裡？

**回答：** Phase 0 的主要成本不是 marker classification，而是 browser round-trip、PDF download、hashing 與之後的 Gemini/Firestore work。可以用 bounded retries、deduplication key、streaming/hash-on-write 與 cached safe metadata 降低重複工作；不能用無限 retry 掩蓋 session failure。

#### E2. 如何避免 secrets 泄漏？

**回答：** secret never enters Git, logs, Telegram or Firestore；extension 不讀 cookie/token；diagnostics 僅輸出 allow-listed fields；測試 fixture 使用 `secret` 佔位字串並 assert redaction；錯誤訊息不回傳 raw URL/body。這是 data-flow constraint，不只是 code review convention。

#### E3. 為什麼 `UNKNOWN` 要比 aggressive retry 更安全？

**回答：** unknown 可能是網站改版、網路 timeout 或真的 logout。無限 retry 可能造成帳號鎖定、重複下載或外部副作用。有限 retry 加明確通知能保留可觀測性與安全邊界。

### F. Further thinking

#### F1. Phase 1 的 reply correlation 為什麼不能只看文字？

**回答：** 一般聊天可能剛好包含課名或頁碼。系統應要求 Telegram Reply 指向特定 progress prompt，並用 message id、course id、class date 做 correlation；文字只作為 payload，不作為唯一 identity。

#### F2. Firestore state 要保存什麼？

**回答：** 至少保存 session id、course、class date、prompt message id、reminder count、status、reply message id、reported progress、created/updated timestamps 與最後錯誤。需要 deduplication key，才能在 webhook retry 或 scheduler retry 時保持 idempotent。

#### F3. 如果官方校曆解析不確定，應不應發課後問題？

**回答：** 不應猜。只有明確 `no_class`、`normal_instruction` 或 `exam_period` 才改變流程；未知或矛盾事件應 `needs_confirmation`，避免錯過課程或在假日打擾使用者。

## 2. 知識分級與學習價值

| Level | 主題 | 為什麼需要學 | 能解決什麼問題 | 能精確理解的工程決策 | 面試／專案／工作用途 |
|---|---|---|---|---|---|
| Must know | CAS、ticket、session | 這是登入可靠性的根本 | 分辨「驗證成功」與「服務 session 成功」 | 為何 REST 可能失敗而 browser 成功 | 解釋 SSO、OAuth-like flows、debug auth |
| Must know | HTTP status vs application state | 200 不代表業務成功 | 避免 false positive | 為何要檢查 redirect 與 page marker | API integration、incident diagnosis |
| Must know | CSRF、cookie、secret hygiene | browser 自動帶 cookie 會產生風險 | 避免 session hijack 與 log leakage | 為何 connector 只做 redacted observation | web security、code review |
| Must know | State machine、typed result | 失敗不是只有 exception | 安全處理 reauth、unknown、deferred | 為何 fail closed 比 silent empty 更好 | distributed systems、retries |
| Must know | Hash、PDF integrity、evidence scope | 檔案可下載不等於可使用 | 證明 bytes 完整且可重現 | 為何一個 attachment 的結果不能外推全部 | data pipeline、artifact storage |
| Should implement | `BrowserConnector` / `BrowserSessionAdapter` | 把 browser boundary 變成可測試介面 | 不依賴真實 Chrome 也能測 state transition | 為何 fake connector 是重要 seam | unit testing、adapter design |
| Should implement | safe URL/diagnostic redaction | secrets 常藏在 query/header/body | 讓 log 可用但不洩漏 credential | allow-list observability | production debugging |
| Should implement | idempotent Firestore state | webhook/scheduler 會重試 | 防止重複提醒與重複摘要 | source of truth 與 dedup key | cloud backends |
| Should implement | progress prompt/reply correlation | 自然語言可能 ambiguous | 正確綁定課程與日期 | message id 比全文本可靠 | Telegram/chat workflows |
| Worth exploring | CAS protocol details | 深入理解 ticket lifecycle | 分析 callback/cookie failure | 何時值得做 REST adapter | SSO integration |
| Worth exploring | browser extension security model | extension 是新 privilege boundary | 限制 host permissions 與 bridge | 為何 loopback receiver 要驗證 origin | browser security |
| Worth exploring | Firestore transactions and indexes | scheduler/webhook 可能競爭寫入 | 保持 atomic state transition | transaction read/write ordering | distributed consistency |
| Worth exploring | PDF parsing and object streams | PDF 不是單純文字檔 | 分辨 valid bytes 與可抽取文字 | 何時交給 parser/Gemini | document systems |

每個主題都值得學，因為它不只是 Chronos 專用知識：它能讓你看懂「系統到底知道了什麼」「哪個 failure boundary 壞了」「為何設計選擇會犧牲某些便利換取安全與可靠性」。

## 3. One-page summary

**Problem:** 用學校 CAS 登入 TronClass，安全地讀課程資料與附件，不能因為頁面回 200 就誤判成功。

**Architecture:** Chrome owns authentication；extension 送 redacted observation；local adapter 做 state classification；Firestore 未來保存 idempotent course state。

**Key states:** `READY`（可讀）、`REAUTH_REQUIRED`（看見 CAS login）、`UNKNOWN`（證據不足）、`DEFERRED_ATTACHMENT`（metadata 可見但附件保存未證明）。

**Evidence:** REST login 2/2 不可靠；browser read-only flow 可行；一個 PDF 5/5 repeated downloads identical（466,300 bytes、14 pages、same SHA-256）；session 30/60 秒仍 authenticated；exact expiry unknown。

**Security rule:** secrets never enter code, Git, logs, Telegram or Firestore；不送出作業、不修改 TronClass。

**Interview sentence:** “I treated the browser as the authentication boundary and used explicit, redacted observations. A 200 response was not sufficient evidence of authentication, so the adapter fails closed with `reauth_required`, `unknown`, or `deferred_attachment`.”

## 4. 建議學習順序

1. 先畫 CAS → TronClass redirect/session flow，確認 ticket 與 cookie 的角色。
2. 用 `ChromeBrowserConnector.observe()` 理解 safe observation 與 state mapping。
3. 閱讀 `ReadResult`、`SessionState` 與 fake connector tests，自己新增一個 `UNKNOWN` case。
4. 用五份 PDF sample 重跑 integrity checks，理解 checksum 只能證明 bytes 相同，不能證明內容正確無誤。
5. 學 Telegram reply correlation、Firestore transaction 與 deduplication key，再進入 Phase 1。
6. 最後才研究 CAS REST protocol、extension permissions 與長時間 session expiry。

## 5. 可驗證理解的實作練習

1. **State transition exercise：** 用 fake connector 依序餵入 authenticated page、CAS redirect、timeout，assert `READY → REAUTH_REQUIRED → UNKNOWN`，且每個狀態的 `ReadResult` 不洩漏值。
2. **Redaction exercise：** 給 local bridge 一個含 `?ticket=secret`、fragment 與 userinfo 的 URL，確認保存結果只有 origin/path。
3. **PDF integrity exercise：** 對五份 sample 計算 size、SHA-256、PDF magic bytes、EOF 與 page count；把其中一份改成 HTML，確認分類為 deferred/failure。
4. **Reply correlation exercise：** 建立兩個同名課程的 prompt，只有 reply-to message id 正確時才接受 progress。
5. **Idempotency exercise：** 對同一 update id 呼叫兩次，確認 Firestore state 只有一次 reminder 或一次 session transition。
6. **Failure injection exercise：** 模擬 timeout、CAS redirect、marker change 與 Firestore conflict，確認不會把 failure 轉成 empty data。

## 6. 只會背答案但不理解原理的危險說法

- 「200 就是成功」：忽略 application-level authentication evidence。
- 「有 service ticket 就已登入」：混淆 CAS authentication 與 TronClass session。
- 「下載成功一次就穩定」：沒有 repeated sample、hash 與 scope limitation。
- 「session 過期就重新讀 cookie」：忽略 credential boundary 與安全風險。
- 「解析不到課程就當成沒有課」：把 adapter failure 誤判成 empty result。
- 「retry 越多越可靠」：忽略 account lockout、duplicate side effects 與 backoff。
- 「extension 能看到頁面就能讀所有資料」：忽略 host permissions、redaction 與 least privilege。

真正理解的標準是：你能指出 evidence、failure boundary、trade-off，並說明下一個安全而可重現的驗證步驟。

## 7. 目前資訊缺口與 Phase 1 邊界

本文件不把未完成項目包裝成成果。Phase 1 仍需實作並驗證：課表資料模型、下課 prompt、Telegram reply correlation、兩次提醒與 missed state、Firestore atomic/idempotent state，以及與現有 owner-only webhook 的整合。Phase 1 不應偷偷加入 PDF summary、assignment submission 或任何 TronClass write operation；那些屬於後續 phase。

參考實作與證據：

- `docs/study-module-phase0-feasibility.md`
- `docs/study-module-pdf-repeatability-evidence.md`
- `docs/study-module-browser-session-adapter-design.md`
- `chronos/study_adapter.py`
- `chronos/pdf_persistence.py`
- `tests/test_study_adapter.py`
- `tests/test_pdf_persistence.py`
