import asyncio
from unittest.mock import AsyncMock
import pytest
from chronos.ai import AIError,ExternalAI,TaskEditOutput,FieldEdit
from chronos.settings import Settings


@pytest.mark.parametrize('text',[
    '改名為 Draft，內容和日期維持',
    '更名為 Draft，保留內容',
    'Change the title to Draft; keep content unchanged',
    '改名為 Draft，內容中保留「only change content」這句原文',
])
def test_explicit_rename_can_preserve_mentioned_content(monkeypatch,text):
    ai=ExternalAI(Settings(_env_file=None))
    fields={k:{'op':'keep','value':None} for k in TaskEditOutput.model_fields}
    fields['title']=FieldEdit(op='set',value='Draft')
    monkeypatch.setattr(ai,'_generate_output',AsyncMock(return_value=TaskEditOutput(**fields)))
    task={'title':'Original','project':'db','due_at':None,'timing':{'content':'Retain details','estimated_minutes':60}}
    parsed=asyncio.run(ai.edit(task,text))
    assert parsed.title=='Draft' and parsed.project=='db'
    assert parsed.timing['content']=='Retain details' and parsed.timing['estimated_minutes']==60


@pytest.mark.parametrize('text',[
    '修改內容為：新的名字叫做 Draft',
    '內容改成：把標題改為 Draft',
    '只改內容，不改標題',
    '修改內容為「請改名為 Draft」',
])
def test_content_payload_cannot_authorize_title_mutation(monkeypatch,text):
    ai=ExternalAI(Settings(_env_file=None))
    fields={k:{'op':'keep','value':None} for k in TaskEditOutput.model_fields}
    fields['title']=FieldEdit(op='set',value='Draft')
    monkeypatch.setattr(ai,'_generate_output',AsyncMock(return_value=TaskEditOutput(**fields)))
    with pytest.raises(AIError,match='preserve'):
        asyncio.run(ai.edit({'title':'Original'},text))
