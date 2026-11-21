import pytest
from chronos.study_constraints import rest_minutes
from test_study_revision_scenarios import scenario


@pytest.mark.parametrize('text,expected',[
    ('兩個之間不要休息',0),('不用休息',0),('no breaks between sessions',0),
    ('休息改成2分鐘',2),('休息5分鐘，改成休息10分鐘',10),
    ('每段5分鐘的短休息',5),('每段休息3分鐘，後來不用休息',0),
    ('不要休息5分鐘',0),('breaks of 7 minutes',7),
])
def test_literal_rest_corrections_and_negation(text,expected):
    assert rest_minutes(text)==expected


@pytest.mark.parametrize('text',['休息1.5分鐘','休息-5分鐘','-3分鐘休息'])
def test_invalid_literal_rest_does_not_silently_round_or_default(text):
    with pytest.raises(ValueError,match='整數'):
        rest_minutes(text)


def test_named_study_day_also_reserves_requested_breaks(scenario):
    from datetime import datetime,timedelta
    s=scenario
    first=s.task('ISPM Chapter 1');second=s.task('ISPM Chapter 2')
    s.model([(first,60),(second,60)])
    result=s.run('明天 ISPM ch1 和 ch2 各讀一小時，中間休息10分鐘')
    review=s.review()
    assert review and review['break_minutes']==10,result
    assert len(review['revision_breaks'])==1
    rest=review['revision_breaks'][0]
    assert datetime.fromisoformat(rest['end'])-datetime.fromisoformat(rest['start'])==timedelta(minutes=10)
    work=sorted(review['revision_additions'],key=lambda item:item['start'])
    assert work[0]['end']==rest['start'] and rest['end']<=work[1]['start']
    s.bind();s.allow(s.review())
    holds=s.db.get_task_action_state('planning:holds')
    assert len(holds['slots'])==1
