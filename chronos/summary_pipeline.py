"""Coordinate explicit selection, generation, canonical storage and delivery."""
from dataclasses import dataclass
import hashlib
import re
import asyncio
from .note_record import NoteRecord, NoteSource
from .note_delivery import deliver_summary
from .study_notes import SummaryDraft, render_note
from .summary_jobs import SummaryJobs
from .pdf_validation import isolated_pdf_page_count
from .page_segments import page_segments, POLICY_VERSION
from .study_notes import _plain


class GenerationRejected(Exception):
    """Provider explicitly rejected execution; a bounded retry is permitted."""


class GenerationUnavailable(RuntimeError):
    """Explicit HTTP 503; eligible for durable bounded backoff, not inline retry."""


@dataclass(frozen=True)
class PdfInput:
    data: bytes
    page_count: int  # Must come from the PDF parser, not model or browser metadata.


async def generate_selected_summary(db, generator, telegram, *, selection, pdfs,
                                    course, class_date, chat_id, model, prompt_version, now,
                                    segment=None):
    key = selection.generation_key(model=model, prompt_version=prompt_version)
    if segment is not None:
        if len(segment) != 4 or any(type(value) is not int for value in segment):
            raise ValueError("invalid segment")
        start, end, ordinal, total = segment
        if (len(selection.selected_ids) != 1 or set(pdfs) != selection.selected_ids
                or not 1 <= start <= end <= next(iter(pdfs.values())).page_count
                or end - start > 3 or not 1 <= ordinal <= total):
            raise ValueError("invalid segment")
        key = hashlib.sha256(f"{key}:{POLICY_VERSION}:{segment}".encode()).hexdigest()
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
        if await asyncio.to_thread(isolated_pdf_page_count, source.data) != source.page_count:
            raise ValueError("PDF page count mismatch")
    jobs = SummaryJobs(db)
    claim = jobs.claim(key, now)
    if claim is None:
        state = db.get_summary_job(key)
        if state and state["status"] in {"retry", "failed", "uncertain"}:
            return state["status"]
        # A completed job without its canonical note is inconsistent, not pending.
        if state and state["status"] == "completed":
            return "uncertain"
        return "generation_pending"
    try:
        generation_pdfs = pdfs
        if segment is not None:
            start, end, ordinal, total = segment
            item = chosen[0]
            sliced = await asyncio.to_thread(isolated_pdf_page_count, pdfs[item.source_id].data,
                                             page_range=(start, end))
            generation_pdfs = {item.source_id: PdfInput(sliced, end - start + 1)}
        raw = await generator.generate(progress=selection.reported_progress, pdfs=generation_pdfs,
                                       model=model, prompt_version=prompt_version)
        draft = SummaryDraft.model_validate(raw)
        if segment is not None:
            draft.validate_sources({identity: pdf.page_count for identity, pdf in generation_pdfs.items()})
            for point in (*draft.scope, *draft.concepts, *draft.relationships, *draft.exam_inferences):
                for citation in point.citations:
                    citation.page += start - 1
        markdown = render_note(draft, filenames={item.source_id: item.filename for item in chosen},
                               page_counts={identity: pdf.page_count for identity, pdf in pdfs.items()})
        count = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", markdown))
        if segment is None and not 1500 <= count <= 2500:
            raise ValueError("summary Chinese character count outside target")
        if segment is not None:
            markdown = f"# {_plain(item.filename)} — p. {start}–{end} · {ordinal}/{total}\n\n" + markdown
        record = NoteRecord(content_fingerprint=key, course_id=selection.course_id, course=course,
            class_date=class_date, reported_progress=selection.reported_progress,
            sources=[NoteSource(source_id=item.source_id, filename=item.filename, sha256=item.sha256,
                     page_count=pdfs[item.source_id].page_count, uploaded_at=item.uploaded_at) for item in chosen],
            markdown=markdown, model=model, prompt_version=prompt_version, created_at=now,
            **(dict(page_start=start, page_end=end, segment_index=ordinal,
                    segment_total=total, pagination_version=POLICY_VERSION) if segment is not None else {}))
        db.save_study_note(record)
    except GenerationUnavailable:
        return jobs.finish(key, claim, now, outcome="unavailable")["status"]
    except GenerationRejected:
        return jobs.finish(key, claim, now, outcome="rejected")["status"]
    except Exception:
        # No provider errors/content in logs or user replies. Unknown outcome or
        # invalid generated content cannot authorize another model invocation.
        jobs.finish(key, claim, now, outcome="uncertain")
        return "uncertain"
    jobs.finish(key, claim, now, outcome="completed")
    return await deliver_summary(db, telegram, chat_id=chat_id, fingerprint=key, now=now)


async def generate_selected_segments(db, generator, telegram, *, selection, pdfs, **kwargs):
    """Stable file order; each saved segment resumes through the delivery ledger."""
    from dataclasses import replace
    from datetime import timedelta
    from time import monotonic
    started = monotonic()
    if not selection.confirmed or set(pdfs) != selection.selected_ids:
        raise ValueError("confirmed matching selection required")
    for item in sorted(selection.catalog, key=lambda item: item.source_id):
        if item.source_id not in selection.selected_ids:
            continue
        single = replace(selection, catalog=(item,), selected_ids=frozenset({item.source_id}))
        ranges = page_segments(pdfs[item.source_id].page_count)
        for ordinal, (start, end) in enumerate(ranges, 1):
            result = await generate_selected_summary(db, generator, telegram, selection=single,
                pdfs={item.source_id: pdfs[item.source_id]}, segment=(start, end, ordinal, len(ranges)),
                **{**kwargs, "now": kwargs["now"] + timedelta(seconds=monotonic() - started)})
            if result != "sent":
                return result
    return "sent"
