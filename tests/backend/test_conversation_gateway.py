import io
import json
from dataclasses import replace

import pytest

from supplier_comparison.backend.conversations import ConversationModelConfig, _call_conversation_model
from supplier_comparison.rag.clients import ModelClientError

CONFIG = ConversationModelConfig('openai-compatible', 'model', 'https://invalid.test/v1', 'TEST_GATEWAY_KEY', environment='ORGANIZER')

def request(monkeypatch, choice, *, config=CONFIG):
    monkeypatch.setenv('TEST_GATEWAY_KEY', 'synthetic')
    bodies = []
    def opener(req, **kwargs):
        bodies.append(json.loads(req.data))
        return io.BytesIO(json.dumps({'choices': [choice]}).encode())
    result = _call_conversation_model(config, [], opener=opener, sleeper=lambda _: None,
                                      output_schema={'type': 'object', 'properties': {'route': {'type': 'string'}}})
    return result, bodies[0]

def tool(name='submit_decision_response', arguments='{"route":"EXPLAIN"}'):
    return {'type': 'function', 'function': {'name': name, 'arguments': arguments}}

def test_organizer_output_tool_preserves_schema(monkeypatch):
    (result, attempts), body = request(monkeypatch, {'finish_reason': 'tool_calls', 'message': {'tool_calls': [tool()]}})
    assert attempts == 1
    assert result['choices'][0]['finish_reason'] == 'stop'
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}
    assert 'response_format' not in body and 'enable_thinking' not in body
    assert body['tools'][0]['function']['parameters']['properties'] == {'route': {'type': 'string'}}

def test_single_fenced_content_is_accepted(monkeypatch):
    request(monkeypatch, {'finish_reason': 'end_turn', 'message': {'content': '```json\n{"route":"EXPLAIN"}\n```'}})

@pytest.mark.parametrize('message,finish', [
    ({'tool_calls': [tool('wrong')]}, 'tool_calls'),
    ({'tool_calls': [tool(), tool()]}, 'tool_calls'),
    ({'tool_calls': [tool(arguments='{}{}')]}, 'tool_calls'),
    ({'content': '{}{}'}, 'stop'),
    ({'content': 'prose {}'}, 'stop'),
    ({'content': '{}'}, 'length'),
])
def test_ambiguous_or_incomplete_output_rejected(monkeypatch, message, finish):
    with pytest.raises(ModelClientError):
        request(monkeypatch, {'finish_reason': finish, 'message': message})

def test_local_protocol_unchanged(monkeypatch):
    _, body = request(monkeypatch, {'finish_reason': 'stop', 'message': {'content': '{}'}}, config=replace(CONFIG, environment='LOCAL'))
    assert body['response_format'] == {'type': 'json_object'}
    assert 'tools' not in body
