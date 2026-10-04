"""Isolated synthetic Phase 2 Firestore verification; never prints credentials."""
from datetime import datetime, timezone, timedelta
import json
import argparse
import logging
import os
from pathlib import Path
import subprocess
from uuid import uuid4
from google.oauth2.credentials import Credentials
from chronos.firestore_db import FirestoreDatabase
from chronos.note_record import NoteRecord, NoteSource
from chronos.selection_store import SelectionStore, decode
from chronos.study_materials import MaterialSelection, PdfMaterial
from chronos.summary_jobs import SummaryJobs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--default-credentials', action='store_true', help='Use the worker authentication path instead of a CLI access token')
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    evidence = {"note_roundtrip": False, "first_result_preserved": False,
                "selection_roundtrip": False, "job_completed": False, "cleanup_complete": False,
                "segment_metadata_roundtrip": False, "retry_roundtrip": False,
                "execution_binding_roundtrip": False}
    references = []
    db = None
    try:
        gcloud = Path(os.environ["LOCALAPPDATA"]) / "Google/Cloud SDK/google-cloud-sdk/bin/gcloud.cmd"
        project = subprocess.run([str(gcloud), "config", "get-value", "project"], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
        if project != "yili-chronos-prod":
            raise ValueError("unexpected project; no writes allowed")
        if args.default_credentials:
            credentials = None
        else:
            token = subprocess.run([str(gcloud), "auth", "print-access-token"], capture_output=True, text=True, check=True, timeout=30).stdout.strip()
            credentials = Credentials(token)
        prefix = "study_phase2_verify_" + uuid4().hex
        db = FirestoreDatabase(project, "(default)", prefix, credentials=credentials)
        fingerprint = "a" * 64
        references = [db.study_notes.document(fingerprint), db.material_selections.document("synthetic"), db.summary_jobs.document(fingerprint)]
        now = datetime.now(timezone.utc)
        record = NoteRecord(content_fingerprint=fingerprint, course_id="synthetic", course="Synthetic verification",
            class_date=now.date(), reported_progress="No real lecture content", markdown="# Synthetic note\n\nUTF-8 中文。\n",
            sources=[NoteSource(source_id="1", filename="synthetic.pdf", sha256="b" * 64, page_count=6)],
            model="none", prompt_version="test", created_at=now,
            page_start=4, page_end=6, segment_index=2, segment_total=2, pagination_version="physical-three-v1")
        db.save_study_note(record)
        repeated = db.save_study_note(record.model_copy(update={"markdown": "must not overwrite"}))
        evidence["first_result_preserved"] = repeated == record
        selection = MaterialSelection("synthetic-session", "synthetic", "No real lecture content",
            (PdfMaterial("1", "synthetic", "synthetic.pdf", None, "b" * 64),)).choose("1", selected=True).confirm()
        store = SelectionStore(db)
        store.create("synthetic", 1, selection)
        store.bind_message("synthetic", 1, 1)
        store.freeze_content("synthetic", 1, selection)
        execution = dict(model="synthetic-model", prompt_version="synthetic-prompt",
                         pagination_version="physical-three-v1")
        store.bind_execution("synthetic", 1, **execution)
        reopened = FirestoreDatabase(project, "(default)", prefix, credentials=credentials)
        resumed_store = SelectionStore(reopened)
        resumed_store.bind_execution("synthetic", 1, **execution)
        mismatch_rejected = False
        try:
            resumed_store.bind_execution("synthetic", 1, **{**execution, "model": "changed-model"})
        except ValueError:
            mismatch_rejected = True
        evidence["execution_binding_roundtrip"] = mismatch_rejected and reopened.get_material_selection("synthetic").get("execution") == execution
        evidence["note_roundtrip"] = reopened.get_study_note(fingerprint) == record and reopened.list_study_notes("Synthetic verification") == [record]
        evidence["selection_roundtrip"] = decode(reopened.get_material_selection("synthetic")["selection"]) == selection
        loaded = reopened.get_study_note(fingerprint)
        evidence["segment_metadata_roundtrip"] = (loaded.page_start, loaded.page_end, loaded.segment_index,
            loaded.segment_total, loaded.pagination_version) == (4, 6, 2, 2, "physical-three-v1")
        jobs = SummaryJobs(reopened)
        claim = jobs.claim(fingerprint, now)
        if not claim or jobs.claim(fingerprint, now) is not None:
            raise ValueError("exclusive claim failed")
        retry = jobs.finish(fingerprint, claim, now, outcome="unavailable")
        due = datetime.fromisoformat(retry['next_retry_at'])
        restarted = SummaryJobs(FirestoreDatabase(project, "(default)", prefix, credentials=credentials))
        early = restarted.claim(fingerprint, due-timedelta(seconds=1))
        claim = restarted.claim(fingerprint, due)
        evidence['retry_roundtrip'] = early is None and claim is not None and reopened.get_summary_job(fingerprint)['attempt_count'] == 2
        evidence["job_completed"] = restarted.finish(fingerprint, claim, due, outcome="completed")["status"] == "completed"
    except Exception as error:
        evidence["error_type"] = type(error).__name__
    finally:
        if references:
            try:
                for reference in references:
                    reference.delete(timeout=15)
                evidence["cleanup_complete"] = all(not reference.get(timeout=15).exists for reference in references)
            except Exception:
                evidence["cleanup_complete"] = False
        print(json.dumps(evidence))
    return 0 if all(evidence.get(key) for key in ("note_roundtrip", "first_result_preserved", "selection_roundtrip", "job_completed", "cleanup_complete", "segment_metadata_roundtrip", "retry_roundtrip", "execution_binding_roundtrip")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
