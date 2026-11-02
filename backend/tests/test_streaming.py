import io
import json
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from app.config import Settings
from app.model_client import ModelClient
from app.streaming import event_sink, partial_strings, stream_response
from tests import test_service


class StreamingTest(unittest.TestCase):
    def test_partial_json_holds_escapes_and_ignores_nested_fields(self):
        self.assertEqual(partial_strings('{"tool":{"answer":"private"},"answer":"a\\n\\u4e'), {"answer": "a\n"})
        self.assertEqual(partial_strings('{"answer":"a\\n\\u4e2d\\"b"}')["answer"], 'a\n中"b')

    def test_real_model_protocol_emits_before_completion_and_rejects_truncation(self):
        events = []
        raw = json.dumps({"answer": "销售额\n来自真实结果", "tool_calls": []}, ensure_ascii=False)
        client = ModelClient(Settings(api_key="test"))

        def lines():
            for char in raw:
                yield ('data: ' + json.dumps({"choices": [{"delta": {"content": char}}]}) + '\n\n').encode()
            yield b'data: [DONE]\n\n'

        response = io.BytesIO(b''.join(lines()))
        token = event_sink.set(events.append)
        try:
            with patch('urllib.request.urlopen', return_value=response) as call:
                result = client.chat_json('你是数据问答智能体', '问题')
            self.assertTrue(json.loads(call.call_args.args[0].data)["stream"])
            self.assertGreater(len(events), 3)
            self.assertEqual(events[-1]["text"], result["answer"])
            with patch('urllib.request.urlopen', return_value=io.BytesIO(b'data: {"choices":[{"delta":{"content":"{"}}]}\n\n')):
                with self.assertRaisesRegex(RuntimeError, '中断'):
                    client.chat_json('你是数据问答智能体', '问题')
        finally:
            event_sink.reset(token)

    def test_stream_graph_query_report_and_clarification_keep_same_contract(self):
        # Existing deterministic model executes actual LangGraph, MCP and DuckDB.
        case = test_service.AskDataServiceTest()
        case.setUp()
        try:
            service = case.service
            def collect(operation):
                return [json.loads(frame[6:]) for frame in stream_response(operation) if frame.startswith('data:')]
            query = collect(lambda: service.submit('查询本月各地区销售额', 'stream-session'))
            self.assertEqual(query[-1]['result']['status'], 'completed')
            self.assertTrue(query[-1]['result']['rows'])
            self.assertTrue(any(e['event']=='table' for e in query[:-1]))
            source = query[-1]['result']['task_id']
            report = collect(lambda: service.submit('生成分析报告', 'stream-session', {'analysis_table_ids':[source]}))
            self.assertTrue(report[-1]['result']['report']['markdown'])
            self.assertEqual(report[-1]['result']['analysis_sources'][0]['task_id'], source)
            waiting = collect(lambda: service.submit('歧义查询各地区销售额', 'stream-hitl'))[-1]['result']
            self.assertEqual(waiting['status'],'waiting_clarification')
            resumed = collect(lambda: service.clarify(waiting['task_id'],'orders_current'))[-1]['result']
            self.assertEqual(resumed['task_id'], waiting['task_id'])
            self.assertEqual(resumed['status'],'completed')
        finally:
            case.tearDown()

    def test_http_auth_errors_and_sse_final_event(self):
        from app.main import app
        from app.api import routes
        case = test_service.AskDataServiceTest()
        case.setUp()
        try:
            with patch.object(routes, 'service', case.service), TestClient(app) as client:
                self.assertEqual(client.post('/api/query/stream',json={'query':'你好'}).status_code,401)
                token = client.post('/api/auth/login',json={'username':'admin','password':'admin123'}).json()['access_token']
                headers = {'Authorization':f'Bearer {token}'}
                response = client.post('/api/query/stream',headers=headers,json={'query':'你好'})
                self.assertIn('text/event-stream', response.headers['content-type'])
                frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data:')]
                self.assertEqual(frames[-1]['result']['route'],'direct_response')
                self.assertEqual(client.post('/api/tasks/missing/clarify/stream',headers=headers,json={'option_id':'x'}).status_code,404)
        finally:
            case.tearDown()
