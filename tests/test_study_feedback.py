import pytest
from chronos.study_feedback import extract

@pytest.mark.parametrize('text,minutes', [
 ('讀了兩小時，到第20頁',120), ('兩個半小時',150), ('半個小時',30),
 ('一小時半',90), ('一小時30分鐘',90), ('1.5 hours, page 20',90),
 ('休息30分鐘，專心讀了兩小時，到第20頁',120),
 ('原訂兩小時，實際讀30分鐘，到第20頁',30),
])
def test_explicit_duration(text, minutes):
    assert extract(text)['minutes'] == minutes

@pytest.mark.parametrize('text', ['預計兩小時，到第20頁', '總共兩小時包含休息30分鐘，到第20頁'])
def test_never_treat_planned_or_break_inclusive_time_as_focus(text):
    assert 'minutes' not in extract(text)

def test_known_pages_are_explicit_not_inferred():
    assert extract('到第二十頁，總共四十八頁，下次從二十一頁開始') == {'completed':20,'total':48,'next_page':21}
    assert extract('讀了20頁') == {}


def test_date_and_next_page_are_not_completed_page_measurements():
    values = extract('10/28 讀了兩小時，到第20頁，下次從第21頁開始')
    assert values['completed'] == 20 and 'total' not in values
    assert extract('下次從第21頁開始') == {'next_page':21}
    assert 'total' not in extract('總共兩小時，到第20頁')
