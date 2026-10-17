# Telegram reply receipt storage

Firestore update receipts store each message's `reply_markup` as a JSON string.
Telegram inline keyboards contain arrays of rows; storing them directly inside
the receipt's message array can fail with `Property array contains an invalid
nested entity`. That failure rolls back the task mutation and receipt together,
so the webhook returns an error without delivering the reply.

Receipt reads decode the markup before delivery, including duplicate webhook
requests. Existing receipts containing map-shaped markup remain readable.
Delivery counters still count the original messages. SQLite storage is unchanged.

Regression coverage in `tests/test_firestore.py` checks the stored representation,
decoded replies, duplicate processing without repeating the action, and partial
delivery tracking. Live release verification must additionally exercise a real
Firestore transaction; the fake database does not enforce Firestore value rules.
