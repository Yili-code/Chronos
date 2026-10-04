"""Conservative weekday-presence check, not date arithmetic or entailment."""
import re
import unicodedata


def validate_inference_evidence(draft, source_pages):
    """Check excerpt provenance, NOT whether evidence entails the prediction.

    Preserve punctuation and word order; tolerate Unicode presentation forms
    and PDF line wrapping only. Missing/unextractable text fails closed.
    """
    def normalized(text):
        return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))

    for inference in draft.exam_inferences:
        if not inference.evidence:
            raise ValueError('exam inference requires source excerpts')
        cited = {(c.source_id, c.page) for c in inference.citations}
        for excerpt in inference.evidence:
            pages = source_pages.get(excerpt.source_id, [])
            if ((excerpt.source_id, excerpt.page) not in cited
                    or not 1 <= excerpt.page <= len(pages)):
                raise ValueError('inference excerpt outside cited pages')
            quote = normalized(excerpt.quote)
            if len(quote) < 12 or quote not in normalized(pages[excerpt.page - 1]):
                raise ValueError('inference excerpt absent from cited page')

WEEKDAYS = tuple(re.compile(pattern, re.I) for pattern in (
    r'星期一|週一|周一|\bmonday\b', r'星期二|週二|周二|\btuesday\b',
    r'星期三|週三|周三|\bwednesday\b', r'星期四|週四|周四|\bthursday\b',
    r'星期五|週五|周五|\bfriday\b', r'星期六|週六|周六|\bsaturday\b',
    r'星期日|星期天|週日|周日|\bsunday\b'))


def weekdays(text):
    return {i for i, pattern in enumerate(WEEKDAYS) if pattern.search(text)}


def requires_weekday_check(draft):
    points = (*draft.scope, *draft.concepts, *draft.relationships, *draft.exam_inferences)
    return any(weekdays(p.text + ' ' + getattr(p, 'rationale', '')) for p in points) or any(weekdays(t) for t in draft.uncertainties)


def validate_weekday_presence(draft, source_pages):
    for point in (*draft.scope, *draft.concepts, *draft.relationships, *draft.exam_inferences):
        evidence = ' '.join(source_pages[c.source_id][c.page - 1] for c in point.citations)
        if not weekdays(point.text + ' ' + getattr(point, 'rationale', '')) <= weekdays(evidence):
            raise ValueError('weekday absent from cited source pages')
    evidence = ' '.join(text for pages in source_pages.values() for text in pages)
    if any(not weekdays(text) <= weekdays(evidence) for text in draft.uncertainties):
        raise ValueError('weekday absent from supplied segment')
