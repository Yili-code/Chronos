# Local contacts and relationship records

This is Chronos's owner-maintained local store, not Google Contacts synchronization. No contact API, invitations or outreach are executed. Data is isolated by configured Gmail account and available only through owner-bound mail cards.

Reply to the person's mail card with `新增聯絡人：Alex；公司：Acme；職稱：工程師`. Name/company/role come only from your explicit text; the email address comes from that selected card. This is provenance, not proof of a sender's real-world identity. Chronos does not infer a company, role, birthday or relationship from an email body.

`查看聯絡人` displays only records matching the selected card's sender. Each record has a stable ID and a changing version. Two same-name people with different addresses remain separate. The same address cannot silently create another record or overwrite an existing one.

## Maintenance

- `更新聯絡人 <ID> <version>：公司：Newco` changes explicit fields only. Stale versions are refused. Previous identity/preference field values are retained in a bounded change history.
- `刪除聯絡人 <ID>` prepares a local deletion; nothing is deleted yet. Confirm with `確認聯絡人操作 <code>`. Codes expire after ten minutes and become invalid if the record changes. Deletion removes the local contact and its notes; existing Telegram messages/update receipts are not erased.
- `合併聯絡人 <ID1> <ID2>` prepares a merge for explicit confirmation. Conflicting nonempty fields and history capacity are rejected rather than arbitrarily chosen. Merged records retain all addresses, original sources and interaction history. Do-not-contact wins if either record has it. A name match never triggers an automatic merge.

An owner-provided ID is an explicit selection within this owner's account, not permission for an external recipient to query records. Incoming email text is never treated as a maintenance command.

## Relationship evidence and preferences

`記錄互動 <ID> <version>：討論合作條件，尚未承諾` stores a short owner-sourced note with an aware timestamp. `關係紀錄 <ID>` displays these notes and explicit preferences. These are owner statements; Chronos does not invent a conversation or treat a mail summary as an interaction that actually happened.

Preferences use `聯絡偏好 <ID> <version>：不主動聯絡`, `允許聯絡`, `每30天`, or `生日11-03`. Birthdays must be valid month/day values. Cadence ranges from seven through 365 days. Setting cadence does not imply consent to automated sending.

`關係建議 <ID>` offers a suggestion only when an explicitly supplied birthday matches today or a supplied interaction date and cadence establish a gap. Do-not-contact suppresses suggestions. No employment, health, cultural practice or family information is inferred from social signals. No greeting is sent or scheduled automatically.

Birthday comparisons and new notes use the configured owner's timezone even when a scheduler supplies UTC. Relation history shows the newest five entries per page; `關係紀錄 <ID> 第 2 頁` accesses older entries. Address lists show three entries per page; use `聯絡方式 <ID> 第 2 頁` for explicitly merged aliases. Pagination retains all stored records and avoids overlong Telegram messages.

## Limits and consistency

The account registry is one transactionally updated document, supporting SQLite and Firestore's read-before-write contract. There are limits of 100 contacts, 20 notes per contact, 10 changes per contact, 20 pending confirmations, and a conservative 700 KB UTF-8 serialized size bound. Operations refuse capacity overflow without dropping history. Removing a contact or explicitly curating data is required before adding data beyond these bounds.

Card-context commands outside a selected mail card request clarification rather than falling through to generic task creation. Contact previews remain bound to the source card. Versions and expiring confirmation codes prevent overlapping owner operations from silently overwriting state.

Regression tests exercise both databases, duplicate identity handling, same-name separation, stale versions, confirmed deletion/merge, preference preservation, birthday validation, sourced history, missing context and real Telegram webhook receipts. External services are mocked; no Google Contacts integration or production validation is implied by these tests.
