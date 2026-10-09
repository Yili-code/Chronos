"""Send one explicit document attachment to the configured Sisyphus private chat.

Secrets remain in memory. The command prints only an allowlisted delivery status.
An unknown outcome must be reviewed before any retry.
"""

import argparse
import logging
from pathlib import Path

import httpx


ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ROOTS = ((ROOT / "output" / "pdf").resolve(), (ROOT / "docs").resolve())
CONFIG = Path.home() / ".codex" / "sisyphus" / "telegram.env"


def load_settings() -> dict[str, str]:
    settings = {}
    for line in CONFIG.read_text(encoding="utf-8-sig").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            settings[key.strip()] = value.strip().strip('"').strip("'")
    return settings


def allowed_document(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not any(resolved == root or root in resolved.parents for root in ALLOWED_ROOTS):
        raise ValueError("document must be under output/pdf or docs")
    if resolved.suffix.lower() != ".pdf" or resolved.stat().st_size > 50_000_000:
        raise ValueError("a PDF no larger than 50 MB is required")
    return resolved


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("document", type=Path)
    parser.add_argument("--caption", required=True)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    try:
        document = allowed_document(args.document)
        settings = load_settings()
        with document.open("rb") as stream:
            response = httpx.post(
                f"https://api.telegram.org/bot{settings['TELEGRAM_BOT_TOKEN']}/sendDocument",
                data={"chat_id": settings["TELEGRAM_CHAT_ID"], "caption": args.caption[:1024]},
                files={"document": (document.name, stream, "application/pdf")},
                timeout=40,
            )
        result = response.json()
        payload = result.get("result", {}) if isinstance(result, dict) else {}
        if response.is_success and result.get("ok") is True and payload.get("document"):
            print(f"document_delivery=confirmed; message_id={payload.get('message_id')}")
            return 0
        print("document_delivery=unconfirmed")
    except Exception:
        print("document_delivery=unknown; review before retry")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
