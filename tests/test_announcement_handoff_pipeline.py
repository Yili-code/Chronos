"""Synthetic loopback-to-canonical-to-Telegram contract, not live acceptance."""
import asyncio
from datetime import datetime
from http.client import HTTPConnection
import json
import threading
from unittest.mock import AsyncMock

import pytest
from chronos.announcement_scheduler import tick_announcements
from chronos.announcement_sync import sync_announcements
from chronos.course_tracking import TAIPEI
from chronos.db import Database
from chronos.local_observation_server import LocalObservationServer
from test_announcements import payload
from test_firestore import database as firestore_fake


@pytest.mark.parametrize('backend', ['sqlite', 'firestore'])
def test_real_http_handoff_import_delivery_and_replay(tmp_path, monkeypatch, backend):
    extension_id = 'a' * 32
    server = LocalObservationServer(0, catalog_path=tmp_path/'catalog.sqlite3', extension_id=extension_id)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    db = firestore_fake(monkeypatch) if backend == 'firestore' else Database(tmp_path/'canonical.sqlite3')
    db.initialize()
    bot = AsyncMock()
    bot.send_message.return_value = {'ok':True, 'result':{'message_id':123}}
    data = payload()
    content = 'Synthetic lecture announcement.\n' * 150
    data['announcements'][0]['content'] = content
    try:
        for iteration in range(2):
            connection = HTTPConnection('127.0.0.1', server.server_port)
            connection.request('POST','/v1/browser-announcements',json.dumps(data),{
                'Origin':'chrome-extension://' + extension_id,
                'Content-Type':'application/json', 'X-Chronos-Bridge':'1'})
            response = connection.getresponse()
            receipt = json.loads(response.read())
            connection.close()
            assert response.status == 202
            assert receipt['inserted'] == (1 if iteration == 0 else 0)
            now = datetime.now(TAIPEI)
            counts = sync_announcements(db,server.announcement_store,now)
            assert counts['imported'] == (1 if iteration == 0 else 0)
            result = asyncio.run(tick_announcements(db,bot,123,now))
            if iteration == 0:
                sends = bot.send_message.await_count
                assert sends > 1
                reconstructed = ''.join(call.args[1].rsplit('\n\n公告分段 ',1)[0]
                                        for call in bot.send_message.call_args_list)
                assert reconstructed.endswith(content)
            else:
                assert result['announcement_parts_sent'] == 0
                assert bot.send_message.await_count == sends
        assert len(db.list_announcements()) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
