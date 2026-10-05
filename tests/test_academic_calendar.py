from datetime import date
import pytest
from chronos.academic_calendar import CalendarEvent, day_policy, parse_calendar


def test_both_semesters_are_parsed():
    html = ''.join(f'<table><tr><th>辦理事項</th></tr><tr><td>{year}年</td><td>{month}月</td><td>（{day}）{text}</td></tr></table>'
                   for year, month, day, text in [(115,'十',9,'國慶日(補假)'),(116,'四',5,'放假')])
    events = parse_calendar(html)
    assert [e.start_date for e in events] == ['2026-10-09','2027-04-05']


def test_invalid_date_is_not_silently_dropped():
    with pytest.raises(ValueError):
        parse_calendar('<table><tr><th>辦理事項</th></tr><tr><td>115年</td><td>二月</td><td>（30）放假</td></tr></table>')


def test_holiday_exam_overlap_and_contradictory_instruction():
    events = [CalendarEvent('2026-10-26','2026-10-26','no_class','補假'),
              CalendarEvent('2026-10-26','2026-10-30','exam_period','期中考試')]
    assert day_policy(events,date(2026,10,26)) == 'no_class'
    assert day_policy(events,date(2026,10,27)) == 'exam_period'
    assert day_policy(events,date(2026,10,31)) == 'unspecified'
    events.append(CalendarEvent('2026-10-26','2026-10-26','normal_instruction','正常上班上課'))
    assert day_policy(events,date(2026,10,26)) == 'needs_confirmation'
