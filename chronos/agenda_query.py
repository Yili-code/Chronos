"""Literal named-event constraints for read-only calendar questions."""
from html import escape
import re
import unicodedata


def normalized(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', value).casefold())


def matching_events(events, keywords):
    terms = [normalized(term) for term in keywords]
    # A punctuation-only condition must not silently become an unfiltered query.
    if any(not term for term in terms):
        return []
    return [event for event in events if all(term in normalized(event.get('summary') or '') for term in terms)]


def empty_named_query(keywords, start, end):
    name = escape(' / '.join(keywords))
    return f'{start:%m/%d %H:%M}–{end:%m/%d %H:%M}\n\n行事曆中沒有相符的 {name} 活動。'
