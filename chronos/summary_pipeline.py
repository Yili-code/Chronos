"""Coordinate explicit selection, generation, canonical storage and delivery."""
from dataclasses import dataclass
import hashlib
import re
from .note_record import NoteRecord, NoteSource
from .note_delivery import deliver_summary
from .study_notes import SummaryDraft, render_note
from .summary_jobs import SummaryJobs
from .pdf_validation import pdf_page_count


class GenerationRejected(Exception):
    """Provider explicitly rejected execution; a bounded retry is permitted."""


@dataclass(frozen=True)
class PdfInput:
    data: bytes
    page_count: int  # Must come from the PDF parser, not model or browser metadata.


async def generate_selected_summary(db, generator, telegram, *, selection, pdfs,
                                    course, class_date, chat_id, model, prompt_version, now):
    key = selection.generation_key(model=model, prompt_version=prompt_version)
    if db.get_study_note(key) is not None:
        return await deliver_summary(db, telegram, chat_id=chat_id, fingerprint=key, now=now)
    chosen = [item for item in selection.catalog if item.source_id in selection.selected_ids]
    if set(pdfs) != selection.selected_ids:
        raise ValueError("PDF inputs must exactly match confirmed selection")
    for item in chosen:
        source = pdfs[item.source_id]
        if hashlib.sha256(source.data).hexdigest() != item.sha256:
            raise ValueError("selected PDF content changed")
        if type(source.page_count) is not int or source.page_count < 1:
            raise ValueError("verified PDF page count required")
        if pdf_page_count(source.data) != source.page_count:
            raise ValueError("PDF page count mismatch")
    jobs = SummaryJobs(db)
    claim = jobs.claim(key, now)
    if claim is None:
        return "generation_pending"
    try:
        raw = await generator.generate(progress=selection.reported_progress, pdfs=pdfs,
                                       model=model, prompt_version=prompt_version)
        draft = SummaryDraft.model_validate(raw)
        markdown = render_note(draft, filenames={item.source_id: item.filename for item in chosen},
                               page_counts={identity: pdf.page_count for identity, pdf in pdfs.items()})
        count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", markdown))
        if not 1500 <= count <= 2500:
            raise ValueError("summary Chinese character count outside target")
        record = NoteRecord(content_fingerprint=key, course_id=selection.course_id, course=course,
            class_date=class_date, reported_progress=selection.reported_progress,
            sources=[NoteSource(source_id=item.source_id, filename=item.filename, sha256=item.sha256,
                     page_count=pdfs[item.source_id].page_count, uploaded_at=item.uploaded_at) for item in chosen],
            markdown=markdown, model=model, prompt_version=prompt_version, created_at=now)
        db.save_study_note(record)
    except GenerationRejected:
        return jobs.finish(key, claim, now, outcome="rejected")["status"]
    except Exception:
        # No provider errors/content in logs or user replies. Unknown outcome or
        # invalid generated content cannot authorize another model invocation.
        jobs.finish(key, claim, now, outcome="uncertain")
        return "uncertain"
    jobs.finish(key, claim, now, outcome="completed")
    return await deliver_summary(db, telegram, chat_id=chat_id, fingerprint=key, now=now)
