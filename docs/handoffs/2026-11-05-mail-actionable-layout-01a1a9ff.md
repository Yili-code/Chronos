# Actionable mail and category layout

- Updated2026-11-05 Asia/Taipei. User explicitly requires implementation,validated deployment and replacing poor pushes. Activities need per-mail action controls;different categories must never share a message;ordinary information must be concise.
- Baseline6b4e911,isolated codex/mail-actionable-layout. Root hook sync supplied ahead0/behind0;other work preserved.
- Scope:optional-action routing distinct from urgency,per-mail bindings for opportunities,category-isolated concise persistent digests. No new registration,email sending or automatic task/calendar creation. Existing fixed34-mail Gemini/repush and rollout authority continues.
- Status:implementation in progress;no replacement push yet. Prior false assumptions are superseded by end-to-end action-control and category tests.

- Implemented optional-action cards independently of urgency:upcoming participation opportunities retain per-mail controls and bindings even when informational. Weekend Practice#31 card carries the existing mail:task callback to its own original mail. No automatic participation/registration or Calendar write.
- Digests now isolate each category and repeat category/date per continuation page. Product updates separate from newsletters;expired information separate. Ordinary entries use short complete-sentence summaries instead of4 long fact paragraphs. Decision facts stay on the individual opportunity card. Retention remains enabled.
- Focused113 tests passed including Weekend callback-to-event booking with mocked Calendar,UTC-to-Taipei time,per-message category isolation and previous action/receipt safeguards. Full Python suite4,582 passed/0 failures in175.62s;141 existing warnings. Documentation129 links/186 files0 missing,diff check passed.
- Same24-case local preview:1 required card,3 optional-action cards (including Weekend),4 category-isolated digests;all4 card targets/bindings validated with dummy owner and mocked transport. Categories notification/newsletter/promotion/expired. No real sends,mailbox writes,Calendar writes or Gemini calls in preview. Real replacement remains pending validated rollout.
