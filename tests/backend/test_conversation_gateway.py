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

def test_object_tool_arguments_are_normalized(monkeypatch):
    choice = {
        'finish_reason': 'tool_calls',
        'message': {'tool_calls': [tool(arguments={'route': 'EXPLAIN'})]},
    }
    (result, _), _body = request(monkeypatch, choice)
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}

def test_anthropic_tool_use_block_is_normalized(monkeypatch):
    choice = {
        'finish_reason': 'end_turn',
        'message': {
            'content': [{
                'type': 'tool_use',
                'name': 'submit_decision_response',
                'input': {'route': 'EXPLAIN'},
            }]
        },
    }
    (result, _), _body = request(monkeypatch, choice)
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}

def test_anthropic_tool_use_block_wins_over_explanatory_text(monkeypatch):
    choice = {
        'finish_reason': 'end_turn',
        'message': {
            'content': [
                {'type': 'text', 'text': 'Submitting the structured result.'},
                {
                    'type': 'tool_use',
                    'name': 'submit_decision_response',
                    'input': {'route': 'EXPLAIN'},
                },
            ]
        },
    }
    (result, _), _body = request(monkeypatch, choice)
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}

def test_legacy_function_call_is_normalized(monkeypatch):
    choice = {
        'finish_reason': 'function_call',
        'message': {
            'function_call': {
                'name': 'submit_decision_response',
                'arguments': {'route': 'EXPLAIN'},
            }
        },
    }
    (result, _), _body = request(monkeypatch, choice)
    assert result['choices'][0]['finish_reason'] == 'stop'
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}

def test_single_text_content_block_is_normalized(monkeypatch):
    choice = {
        'finish_reason': 'end_turn',
        'message': {'content': [{'type': 'text', 'text': '{"route":"EXPLAIN"}'}]},
    }
    (result, _), _body = request(monkeypatch, choice)
    assert json.loads(result['choices'][0]['message']['content']) == {'route': 'EXPLAIN'}

@pytest.mark.parametrize('message,finish', [
    ({'tool_calls': [tool('wrong')]}, 'tool_calls'),
    ({'tool_calls': [tool(), tool()]}, 'tool_calls'),
    ({'tool_calls': [tool(arguments='{}{}')]}, 'tool_calls'),
    ({'content': [
        {'type': 'text', 'text': '{"route":"EXPLAIN"}'},
        {'type': 'text', 'text': '{"route":"SIMULATE"}'},
    ]}, 'end_turn'),
    ({'content': '{}{}'}, 'stop'),
    ({'content': 'prose {}'}, 'stop'),
    ({'content': '{}'}, 'length'),
])
def test_ambiguous_or_incomplete_output_rejected(monkeypatch, message, finish):
    with pytest.raises(ModelClientError) as raised:
        request(monkeypatch, {'finish_reason': finish, 'message': message})
    assert "finish=" in str(raised.value)
    assert "message_keys=" in str(raised.value)

def test_local_protocol_unchanged(monkeypatch):
    _, body = request(monkeypatch, {'finish_reason': 'stop', 'message': {'content': '{}'}}, config=replace(CONFIG, environment='LOCAL'))
    assert body['response_format'] == {'type': 'json_object'}
    assert 'tools' not in body

def test_summary_organizer_uses_structured_schema_and_still_validates_refs(monkeypatch):
    from supplier_comparison.backend import summaries, conversations
    value = {'title': '采购摘要', 'overview': '请先核实待确认事项。',
             'sections': [{'heading': '风险', 'text': '请核实。', 'reference_ids': ['RESULT:1']}],
             'disclaimer': '不构成采购审批。'}
    seen = []
    def call(config, messages, **kwargs):
        seen.append((config, kwargs['output_schema']))
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}]}, 1
    monkeypatch.setattr(conversations, '_call_conversation_model', call)
    config = summaries.SummaryModelConfig('model', 'https://invalid.test', 'UNUSED', environment='ORGANIZER')
    facts = {'references': ['RESULT:1'], 'formal_recommendation_allowed': False}
    result, _ = summaries.generate_summary_narrative(facts, config)
    assert result == value
    assert seen[0][0].environment == 'ORGANIZER'
    assert 'sections' in seen[0][1]['properties']
    value['sections'][0]['reference_ids'] = ['INVENTED:1']
    with pytest.raises(ModelClientError):
        summaries.generate_summary_narrative(facts, config)
