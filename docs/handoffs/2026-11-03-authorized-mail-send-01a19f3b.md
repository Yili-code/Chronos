# Explicitly authorized mail sending

- Updated: 2026-11-03 Asia/Taipei. Continues the user's requested distinction between draft and authorized send. No real-recipient test send is authorized by this development work.
- Branch codex/executive-assistant-audit, base f1c9c0ea6463c011ea5924ac758f455bcb7f533c. Meeting CI pending; no merge/deployment.
- Scope: send only the owner's explicitly reviewed existing native draft, with independent expiring confirmation, fresh source/native checks, immutable approved MIME payload and persistent uncertain outcome. No general send endpoint or AI-selected recipient. CC/BCC/attachments remain unsupported in this flow.
- Official drafts.get/send and draft guide verified. Gmail supports supplying an updated immutable MIME message in drafts.send; this avoids sending unexpected recipients/content from a concurrent native edit. Preflight detects known edits; concurrent edits can still be superseded by the specifically approved payload and are not provider-locked.
- Files planned: Gmail transport, mail_send.py, correspondence/context routing, tests/docs. No migration, current production unchanged. All external writes in tests are mocks.
- Pending: implement/test authorization and race contracts, full regression, prior CI verification before push. Do not claim implemented or deployed yet.

## Validation — 2026-11-03 Asia/Taipei

- User explicitly chose to retain only the configured Gmail account for this release scope. No additional account grant or platform connection requested. LINE/Instagram remain deferred.
- Implemented separate expiring send approval, fresh account/thread/native-draft checks, immutable reviewed MIME submission, transactional sending claim, verified SENT result, and persistent uncertain outcome with no automatic retries. No real-recipient test sends.
- Development repair: preserve recipient local-part casing through draft/contact/meeting identity handling; domain normalization only. Targeted cross-domain regression after this repair: 174 passed.
- Native send/webhook compatibility before address repair: 131 passed. Initial send-stage full suite: 3226 passed/1 warning before address repair; final exact-source suite: **3228 passed**, 1 existing Starlette/httpx deprecation warning, 181.59 seconds. No later source behavior changes.
- Two real synthetic Gemini requests using unchanged local configured model/keys validated ReplyText and Candidate schemas and objective missing-date/explicit-duration constraints. No real mail content used and no messages sent. This is local provider smoke, not production AI evidence.
- Prior meeting commit f1c9c0e CI run 41610244509 verified: Python 3.11, Python 3.12, extension all successful. Final send commit CI still pending.
- Undefined-name lint, compilation and diff checks passed. Docs updated to remove historical no-send claims from current usage; baseline 80-case report remains explicitly historical and its Blocked entries are not retroactively marked Pass.
- Current production remains unchanged. Remaining scope includes richer multi-intent/clarification handling, CC/BCC/attachments and multi-party negotiation; no full executive-assistant acceptance or deployment claim.
- Resume check: interrupted packaging/commit sequence had not committed or pushed this phase. Retried `uv build` successfully produced source archive and wheel. No source changes since the 3228-pass final suite. Only explicit task files will be staged; original root changes remain untouched.
