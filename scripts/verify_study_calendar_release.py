"""Read-only release evidence, printing only allowlisted non-secret metadata."""
import json
import logging
import os
from pathlib import Path
import subprocess

import httpx


def main():
    logging.disable(logging.CRITICAL)
    gcloud = str(Path(os.environ['LOCALAPPDATA']) / 'Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd')
    def cli(*args):
        return subprocess.run([gcloud, *args], check=True, capture_output=True,
                              text=True, timeout=60).stdout.strip()
    try:
        service = json.loads(cli('run', 'services', 'describe', 'chronos', '--project',
                                 'yili-chronos-prod', '--region', 'asia-east1', '--format=json'))
        env = {item['name']: item.get('value') for item in
               service['spec']['template']['spec']['containers'][0].get('env', [])}
        prefix = env.get('CHRONOS_FIRESTORE_COLLECTION_PREFIX') or 'chronos'
        database = env.get('CHRONOS_FIRESTORE_DATABASE') or '(default)'
        token = cli('auth', 'print-access-token')
        base = f'https://firestore.googleapis.com/v1/projects/yili-chronos-prod/databases/{database}/documents/{prefix}_meta/'
        with httpx.Client(timeout=30, follow_redirects=False) as client:
            snapshot = client.get(base + 'academic_calendar', headers={'Authorization': 'Bearer ' + token})
            state = client.get(base + 'academic_calendar_sync', headers={'Authorization': 'Bearer ' + token})
            evidence = {'revision': service['status']['latestReadyRevisionName'],
                        'snapshot_http_status': snapshot.status_code, 'sync_http_status': state.status_code}
            if snapshot.status_code == 200:
                fields = snapshot.json()['fields']
                evidence.update(fetched_at=fields['fetched_at']['stringValue'],
                                event_count=len(fields['events']['arrayValue'].get('values', [])),
                                content_sha256=fields['content_sha256']['stringValue'])
            if state.status_code == 200:
                fields = state.json()['fields']
                evidence.update(sync_status=fields['status']['stringValue'],
                                sync_attempts=int(fields['attempts']['integerValue']))
            print(json.dumps(evidence))
            return 0 if snapshot.status_code == 200 and state.status_code == 200 else 1
    except Exception:
        print(json.dumps({'verification': 'failed', 'detail': 'read-only check unavailable'}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
