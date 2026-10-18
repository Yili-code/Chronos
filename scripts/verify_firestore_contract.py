"""Isolated real-Firestore smoke test. Requires explicit project and ADC credentials."""
import argparse
from uuid import uuid4
from chronos.firestore_db import FirestoreDatabase


def verify(project, credentials=None):
    prefix = 'chronos_verify_' + uuid4().hex
    db = FirestoreDatabase(project, '(default)', prefix, credentials=credentials)
    calls = []
    try:
        def action():
            calls.append(1)
            db.mutate_task_action_state('probe', lambda old: {'status': 'active'})
            return {'messages': [{'text': 'Synthetic contract verification', 'reply_markup': {
                'inline_keyboard': [[{'text': 'Inspect', 'callback_data': 'probe'}]]}}]}
        receipt = db.process_update(1, action)
        duplicate = db.process_update(1, action)
        assert len(calls) == 1
        assert duplicate == receipt
        assert isinstance(receipt['messages'][0]['reply_markup'], dict)
        assert db.get_task_action_state('probe')['status'] == 'active'
        db.mark_update_message_delivered(1, 1)
        assert db.get_update(1)['delivered_count'] == 1
        print('Firestore nested state, receipt serialization, duplicate suppression, and delivery progress: passed')
    finally:
        for collection in (db.updates, db.task_action_state):
            for document in collection.stream():
                document.reference.delete()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True)
    verify(parser.parse_args().project)
