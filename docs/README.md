# 文件入口

## 一般使用者

[English setup and usage](../README.en.md)

先看 [本機 Web 示範](demo.md)，或直接依下面流程安裝：

1. [本機啟動與第一筆代辦](getting-started.md)
2. [Gemini 與 Telegram 指令](usage.md)
3. [Cloud Run + Firestore 部署](deployment.md)（選用、會建立雲端資源）
4. [貢獻與問題回報](../CONTRIBUTING.md)

## Engineering learning

- [Operational reliability and release evidence](engineering-operations-learning.md)：從這次 hardening 提取可重用的 readiness、fail-closed、release provenance、cost telemetry 與 evidence-level 思考框架。

## Study：先確認範圍

Study 的 course schedule、Chrome extension 的課程清單，以及 session publisher 的 GCP project 包含維護者專用設定。`.env.example` 預設關閉 Study，AI generation 的每日上限預設為零。啟動一般 Web / Telegram 代辦不會自動完成教材收集或摘要的配置。

- [Chrome extension 現行行為與權限](../chrome-extension/README.md)：本機傳輸、PDF 與選用 session 交接。
- [Phase 2 狀態紀錄](study-module-phase2-status.md)：已做、未做與依賴條件；內容依文件日期解讀。
- [Phase 1 操作紀錄](study-module-phase1-operations.md)：特定部署的排程操作，包含會發送訊息的步驟。
- [驗證報告](../TEST_REPORT.md)：本機驗證與 production 版本分開記錄。

## 歷史設計與證據

`study-module-prd.md`、`study-module-phase0-*`、`study-module-phase1-*`、`study-module-phase2-*`、`*evidence.md` 與 `*interview-prep.md` 保存了需求、階段設計、操作及訪談背景。它們是工程紀錄，可能記載已被後續程式取代的限制，例如舊 Phase 1 的 holiday / AI 行為；不要把早期文件當成目前功能承諾或一般安裝手冊。現行設定以 `chronos/settings.py`、`.env.example` 與對應程式路徑為準。

回到 [專案首頁](../README.md)。
