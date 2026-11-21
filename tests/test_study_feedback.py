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

@pytest.mark.parametrize('text', ['三個小時完成 4/7', '三個小時完成 4/7 題', '三小時，完成四題，共七題', '三小時，要解七題我完成了四題'])
def test_question_counts_preserve_units(text):
    assert extract(text, unit='questions') == {'minutes': 180, 'unit': 'questions', 'completed': 4, 'total': 7}


def test_bare_fraction_requires_question_context_and_dates_stay_dates():
    assert extract('4/7', unit='questions') == {'unit': 'questions', 'completed': 4, 'total': 7}
    assert extract('4/7') == {}
    assert extract('11/5 讀三小時，完成四題，共七題')['total'] == 7
    assert 'total' not in extract('11/5 讀三小時', unit='questions')

def test_generic_counts_do_not_invent_question_or_page_units():
    assert extract('三小時完成4/7') == {'minutes': 180, 'unit': 'items', 'completed': 4, 'total': 7}
    assert extract('4/7', unit='items') == {'unit': 'items', 'completed': 4, 'total': 7}
    assert extract('完成4/7頁') == {'completed': 4, 'total': 7}
    assert extract('到第4頁', unit='questions') == {'unit': 'pages', 'completed': 4}
