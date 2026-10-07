"""Read-only Gmail setup check and filter preview. Does not send or modify anything."""
import asyncio

from .gmail import GmailClient, GmailError
from .mail_workflow import filter_reason
from .settings import settings


async def preview():
    client = GmailClient(settings)
    await client.verify_account()
    ids, more = await client.unread_ids(settings.gmail_max_messages)
    for identifier in ids:
        mail = await client.read(identifier)
        action = "WOULD TRASH" if filter_reason(mail, settings.gmail_keep_senders) else "KEEP"
        print(f"{action}: {mail['subject']} — {mail['sender']}")
    print(f"Previewed {len(ids)} unread messages. More available: {more}. No changes made.")


if __name__ == "__main__":
    try:
        asyncio.run(preview())
    except GmailError as error:
        raise SystemExit(str(error)) from None
