"""Conservative weekday-presence check, not date arithmetic or entailment."""
import re

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
