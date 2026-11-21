# Mail digest visibility

- Updated2026-11-05 Asia/Taipei. Owner screenshot shows only actionable card and asks where the other mail went.
- Confirmed:23 information entries were sent as4 digest pages but MailWorkflow.deliver used requires_response=false;global MessageExpiry deletes passive output after7seconds. Prior delivery-receipt checks did not prove readable persistence. This was missed during quality rollout.
- Authorized scope:restore those exact4 saved pages persistently to the same verified owner chat,no Gemini request/reclassification/mailbox mutation. Then add explicit retention for reference mail digests while preserving7second expiry elsewhere. Preserve historical receipts;use separate restore delivery keys.
- Status:preparing immediate restore and focused code repair. Root sync hook supplied ahead0/behind0;unrelated work preserved.

- Immediate restore complete:the exact4 saved HTML pages covering23 information entries re-delivered under separate durable restore keys. Existing explicit lifetime override kept only these requested pages;verified0 expiry-ledger entries. No Gemini requests,mailbox writes or task creation. The actionable card was not resent.
- Implemented explicit retain option at MailWorkflow delivery boundary;screening digest pages opt in. No classifier priority or owner-question changes;ordinary completion notices keep7second expiry. Added real TelegramClient lifecycle tests across both stores for retained digest flush/restart and normal completion deletion.
- Focused104 tests passed. Full Python suite running;future automatic retention is not yet deployed. Restored pages are already persistent now. Private mail/delivery IDs remain local/production state;only sanitized counts are shareable.

- Final local full Python suite:4,578 passed/0 failures in213.97s;141 existing deprecation/JUnit warnings. Focused104 passed;documentation129 links/185 files0 missing;diff check passed. These cover retained reference digest flush/restart and unchanged ordinary passive expiry.
- Preparing atomic retain-policy PR. Immediate restored pages remain separate from this deployment;no automatic scan or reclassification needed.
