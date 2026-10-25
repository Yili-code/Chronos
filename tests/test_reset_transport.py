import asyncio
import json
from datetime import datetime

import httpx
import pytest

from chronos.ai import AIError, ExternalAI, TaskOutput
from test_ai import config, mock_provider

NOW=datetime.fromisoformat('2026-10-25T23:20:00+08:00')


@pytest.mark.parametrize('kind',['study','event'])
def test_reset_parsers_cross_real_shared_transport_without_subject_keyerror(monkeypatch,kind):
    calls=[]
    def handler(request):
        payload=json.loads(request.content)
        calls.append(payload)
        schema=payload['generationConfig']['responseJsonSchema']
        assert 'actionable' in schema['properties']
        result={'actionable':False,'question':'Which period?'}
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'text':json.dumps(result)}]}}]})
    mock_provider(monkeypatch,handler)
    ai=ExternalAI(config())
    output=asyncio.run(ai.parse_study_reset('reset study',[],[],None,NOW) if kind=='study' else
                       ai.parse_event_reset('meeting','reset meetings',[],None,NOW))
    assert output.actionable is False and output.question=='Which period?'
    assert len(calls)==1


@pytest.mark.parametrize('kind',['study','event'])
def test_reset_transport_failure_becomes_aierror_not_http500(monkeypatch,kind):
    mock_provider(monkeypatch,lambda request:httpx.Response(400,json={'error':{'message':'invalid request'}}))
    ai=ExternalAI(config())
    with pytest.raises(AIError,match='No (study|Calendar) arrangement was changed'):
        asyncio.run(ai.parse_study_reset('reset study',[],[],None,NOW) if kind=='study' else
                    ai.parse_event_reset('meeting','reset meetings',[],None,NOW))


def test_new_subject_uses_safe_generic_error_copy(monkeypatch):
    mock_provider(monkeypatch,lambda request:httpx.Response(400,json={'error':{'message':'invalid request'}}))
    ai=ExternalAI(config())
    with pytest.raises(AIError,match='Nothing was changed'):
        asyncio.run(ai._generate_output('test','synthetic',TaskOutput.model_json_schema(),TaskOutput,'new operation'))
