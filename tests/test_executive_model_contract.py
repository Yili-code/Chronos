"""Real-provider capture, then deterministic replay through webhook and stores."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from chronos import main
from test_command_reliability import (command_scenario, command_model_config, ScenarioAI, LIVE)

CAPTURE=Path(__file__).parent/'fixtures/executive_report_model.json'
REQUESTS=[
    ('brief-1','整理今天的會議和到期任務，標出還沒確定的時間。','Daily briefing'),
    ('brief-2','Give me today’s combined calendar and deadline briefing.','Daily briefing'),
    ('brief-3','我想看今天行事曆加上截止事項的整合摘要，不要修改。','Daily briefing'),
    ('week-1','給我下週一到週日的規劃建議，檢查過載，先別改行程。','Next week'),
    ('week-2','Plan next week on paper: show capacity and deadlines without moving anything.','Next week'),
    ('week-3','下週工作怎麼安排比較好？請提供整週規劃報表，不要幫我排進去。','Next week'),
    ('time-1','分析過去七個完整日的時間使用，不要把計畫當實際。','Actual reported time'),
    ('time-2','Show my time usage for the previous seven complete local days, with measured versus planned time.','Actual reported time'),
    ('time-3','我想看最近七個完整日的工時分析，缺紀錄就說不知道。','Actual reported time'),
]


@pytest.mark.parametrize('identifier,text,expected',REQUESTS)
def test_captured_report_request(command_scenario,command_model_config,monkeypatch,identifier,text,expected):
    s=command_scenario
    saved=json.loads(CAPTURE.read_text(encoding='utf-8')) if CAPTURE.exists() else {}
    # Capture once on SQLite; both backends replay the exact provider response.
    if LIVE and s['backend']=='firestore':
        from test_command_reliability import ScenarioAI as Parent
        class Replay(Parent):
            async def _generate_output(self,prompt,text,schema,output_model,subject,**kwargs):
                entry=self.captured[self.cursor];self.cursor+=1
                assert entry['subject']==subject
                return output_model.model_validate(entry['output'])
        ai=Replay(None,saved[identifier]['outputs'])
    else:
        ai=ScenarioAI(command_model_config,saved.get(identifier,{}).get('outputs',[]))
    monkeypatch.setattr(main,'ai',ai)
    before=deepcopy(s['db'].list_open_tasks())
    payload={'update_id':777001,'message':{'message_id':777,'chat':{'id':123},'text':text}}
    response=s['client'].post('/telegram/webhook',headers={'X-Telegram-Bot-Api-Secret-Token':'test-hook'},json=payload)
    receipt=s['db'].get_update(777001) or {}
    reply='\n'.join(item['text'] for item in receipt.get('messages',[])) or receipt.get('reply','')
    if LIVE and s['backend']=='sqlite':
        saved[identifier]={'input':text,'outputs':ai.outputs,'reply':reply,'status':response.status_code}
        CAPTURE.parent.mkdir(exist_ok=True)
        CAPTURE.write_text(json.dumps(saved,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    assert response.status_code==200,reply
    assert expected in reply,reply
    assert s['db'].list_open_tasks()==before
    s['calendar'].insert.assert_not_awaited()
    s['calendar'].move_event.assert_not_awaited()
    s['calendar'].delete_event.assert_not_awaited()
