"""Verify the demo ignores existing credentials and leaves owner data intact."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
CHECK = r'''
from pathlib import Path
import runpy
import sys
from unittest.mock import patch
from fastapi.testclient import TestClient

owner = Path("owner.db")
owner.write_bytes(b"owner-data-must-stay-intact")
Path(".env").write_text("CHRONOS_WEB_PASSWORD=demo-must-ignore-this\n", encoding="utf-8")
demo_database = []

def verify(app, *, host, port):
    from chronos import main
    config = main.settings
    assert host == "127.0.0.1" and port == 8000
    assert config.database_backend == "sqlite" and config.database_path != owner
    assert not config.gemini_api_key and not config.telegram_bot_token
    assert config.telegram_chat_id is None and not config.public_base_url
    assert not config.web_password and not config.enable_internal_scheduler
    assert not config.enable_study_tracking
    demo_database.append(config.database_path)
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/").status_code == 200
        tasks = client.get("/api/tasks").json()
        assert {task["title"] for task in tasks} == {"Finish the report", "Read the setup guide"}
        assert client.post("/api/tasks/natural", json={"text": "Synthetic demo check"}).status_code == 503
        assert len(client.get("/api/tasks").json()) == 2
        for task in tasks:
            assert client.post(f"/api/tasks/{task['id']}/complete").status_code == 200
        assert client.get("/api/tasks").json() == []

sys.argv = [sys.argv[1]]
with patch("uvicorn.run", verify), patch("httpx.AsyncClient.request", side_effect=AssertionError("external request")):
    runpy.run_path(sys.argv[0], run_name="__main__")
assert demo_database and not demo_database[0].exists()
assert owner.read_bytes() == b"owner-data-must-stay-intact"
print("Demo isolation, task lifecycle, no external requests and temporary database cleanup: OK")
'''


def main():
    env = os.environ.copy()
    env.update({
        "CHRONOS_DATABASE_BACKEND": "firestore",
        "CHRONOS_DATABASE_PATH": "owner.db",
        "CHRONOS_FIRESTORE_PROJECT_ID": "synthetic-demo-project",
        "CHRONOS_GEMINI_API_KEY": "synthetic-key",
        "CHRONOS_TELEGRAM_BOT_TOKEN": "synthetic-token",
        "CHRONOS_TELEGRAM_CHAT_ID": "123",
        "CHRONOS_PUBLIC_BASE_URL": "https://example.invalid",
        "CHRONOS_ENABLE_INTERNAL_SCHEDULER": "true",
        "CHRONOS_ENABLE_STUDY_TRACKING": "true",
    })
    with tempfile.TemporaryDirectory(prefix="chronos-demo-check-") as directory:
        subprocess.run(
            [sys.executable, "-c", CHECK, str(ROOT / "scripts" / "demo_local.py")],
            cwd=directory, env=env, check=True, timeout=60,
        )


if __name__ == "__main__":
    main()
