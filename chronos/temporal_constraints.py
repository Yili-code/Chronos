"""Check adjacent literal date/weekday assertions independently of AI output."""
from datetime import date, timedelta
import re

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
CN = '一二三四五六日'
ASSERTION = re.compile(
    r'(?P<day>\d{4}[-/]\d{1,2}[-/]\d{1,2}|(?<!\d)\d{1,2}/\d{1,2}|'
    r'今天|明天|後天|后天|\bday after tomorrow\b|\btomorrow\b|\btoday\b)'
    r'\s*(?:[（(]\s*)?(?P<week>(?:星期|週|周)[一二三四五六日天]|'
    r'Mon(?:day)?|Tue(?:sday)?|Wed(?:nesday)?|Thu(?:rsday)?|Fri(?:day)?|Sat(?:urday)?|Sun(?:day)?)(?![a-z])', re.I)


def literal_weekday_conflict(text, now):
    """Ask about conflicting evidence; never choose which assertion to override.

    Quoted task data is excluded. This deliberately checks adjacent assertions,
    not dates from separate task/deadline clauses or arbitrary natural language.
    """
    text = re.sub(r'「[^」]*」|『[^』]*』|"[^"\n]*"', '', text)
    relative = {'今天': 0, 'today': 0, '明天': 1, 'tomorrow': 1,
                '後天': 2, '后天': 2, 'day after tomorrow': 2}
    for match in ASSERTION.finditer(text):
        label, week = match['day'], match['week']
        try:
            if label.casefold() in relative:
                day = now.date() + timedelta(days=relative[label.casefold()])
            else:
                parts = [int(part) for part in re.split(r'[-/]', label)]
                day = date(*parts) if len(parts) == 3 else date(now.year, *parts)
        except ValueError:
            return '日期無效，請確認要安排的日期。'
        weekday = (CN.index(week[-1].replace('天', '日')) if not week.isascii()
                   else next(i for i, name in enumerate(DAYS) if name.casefold().startswith(week.casefold())))
        if day.weekday() != weekday:
            return f'「{label}」是 {day.isoformat()}（星期{CN[day.weekday()]}），與「{week}」不一致。要依日期還是星期？'
    return None
