"""Opt-in isolated live Firestore check using the existing gcloud login.

Only synthetic documents under a fresh study_verify UUID prefix are written.
Output contains booleans, never tokens, responses, or exception messages.
"""
import json
import logging
import os
from pathlib import Path
import subprocess
from datetime import date, datetime, timezone
from uuid import uuid4

from google.oauth2.credentials import Credentials
from chronos.firestore_db import FirestoreDatabase
from chronos.course_tracking import COURSE_SCHEDULE, new_session
from chronos.study_delivery import StudyDeliveryLedger


def main():
    logging.disable(logging.CRITICAL)
    evidence = {"live_firestore": False, "cleanup_complete": False}
    db = None
    try:
        gcloud = Path(os.environ['LOCALAPPDATA']) / 'Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd'
        project = subprocess.run([str(gcloud), 'config', 'get-value', 'project'],
            capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        token = subprocess.run([str(gcloud), 'auth', 'print-access-token'],
            capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        credentials = Credentials(token)
        prefix = 'study_verify_' + uuid4().hex
        db = FirestoreDatabase(project, '(default)', prefix, credentials=credentials)
        session = new_session(COURSE_SCHEDULE[0], date(2026, 10, 5), 101)
        assert db.create_course_session(session) == session
        assert db.create_course_session(session) == session
        receipt = db.process_update(1, lambda: db.record_course_reply(101, 102, 'Synthetic integration progress'))
        assert db.process_update(1, lambda: 'duplicate mutation') == receipt
        reopened = FirestoreDatabase(project, '(default)', prefix, credentials=credentials)
        assert reopened.get_course_session(session.session_id).reported_progress == 'Synthetic integration progress'
        ledger = StudyDeliveryLedger(reopened)
        now = datetime.now(timezone.utc)
        claim = ledger.claim('synthetic:prompt', now)
        assert claim and ledger.claim('synthetic:prompt', now) is None
        assert ledger.finish('synthetic:prompt', claim, now, message_id=101)['status'] == 'sent'
        evidence['live_firestore'] = True
    except Exception as error:
        evidence['error_type'] = type(error).__name__
    finally:
        if db is not None:
            try:
                # Only the three exact synthetic documents created above.
                db.course_sessions.document('security:2026-10-05').delete(timeout=15)
                db.updates.document('1').delete(timeout=15)
                db.study_deliveries.document('synthetic:prompt').delete(timeout=15)
                evidence['cleanup_complete'] = True
            except Exception:
                evidence['cleanup_complete'] = False
        print(json.dumps(evidence))
    return 0 if evidence['live_firestore'] and evidence['cleanup_complete'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
