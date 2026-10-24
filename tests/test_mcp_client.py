import json
import unittest
from unittest import mock

from justenough.mcp_client import (
    McpClient,
    McpError,
    _iter_sse_payloads,
    _redact,
    _summarize_content,
    _unwrap_content,
)


class SseTest(unittest.TestCase):
    def test_parses_multiple_events(self):
        body = 'event: message\ndata: {"id":1,"result":{"ok":true}}\n\ndata: {"id":2,"result":{}}\n\n'
        payloads = list(_iter_sse_payloads(body))
        self.assertEqual([p["id"] for p in payloads], [1, 2])

    def test_ignores_comments_and_done(self):
        body = ": ping\ndata: [DONE]\n\n"
        self.assertEqual(list(_iter_sse_payloads(body)), [])

    def test_broken_json_is_skipped(self):
        body = 'data: {oops}\n\ndata: {"id":9}\n\n'
        payloads = list(_iter_sse_payloads(body))
        self.assertEqual(payloads, [{"id": 9}])


class ClientTest(unittest.TestCase):
    def make_client(self):
        return McpClient(token="test-token")

    def test_missing_token_raises(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(McpError):
                McpClient()

    def test_redact_hides_secret(self):
        self.assertNotIn("test-token", _redact("token=test-token", "test-token"))

    def test_initialize_sets_state(self):
        client = self.make_client()
        with mock.patch.object(
            client, "_request", return_value=(200, {}, '{"jsonrpc":"2.0","id":1,"result":{"ok":1}}')
        ):
            client.initialize()
        self.assertTrue(client._initialized)

    def test_401_raises_actionable_error(self):
        client = self.make_client()
        with mock.patch.object(client, "_request", return_value=(401, {}, "当前鉴权码不存在或已失效!")):
            with self.assertRaises(McpError) as ctx:
                client.initialize()
        self.assertEqual(ctx.exception.status, 401)
        self.assertIn("open.mcd.cn", str(ctx.exception))

    def test_429_retries_then_succeeds(self):
        client = self.make_client()
        calls = {"n": 0}

        def fake(payload):
            calls["n"] += 1
            if calls["n"] == 1:
                return (429, {"Retry-After": "0"}, "")
            return (200, {}, json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {"tools": []}}))

        with mock.patch.object(client, "_request", side_effect=fake), mock.patch(
            "justenough.mcp_client.time.sleep"
        ):
            tools = client.list_tools()
        self.assertEqual(tools, [])

    def test_sse_response_is_parsed(self):
        client = self.make_client()
        body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[{"name":"x"}]}}\n\n'
        with mock.patch.object(client, "_request", return_value=(200, {}, body)):
            tools = client.list_tools()
        self.assertEqual(tools[0]["name"], "x")

    def test_session_id_is_reused(self):
        client = self.make_client()
        calls = []

        def fake(payload):
            calls.append(payload)
            if payload.get("method") == "initialize":
                return (200, {"Mcp-Session-Id": "abc"}, '{"jsonrpc":"2.0","id":1,"result":{}}')
            return (200, {}, '{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}')

        with mock.patch.object(client, "_request", side_effect=fake):
            client.list_tools()
        self.assertEqual(client._session_id, "abc")

    def test_tool_error_surfaces_content(self):
        client = self.make_client()
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"isError": True, "content": [{"type": "text", "text": "门店不存在"}]},
            }
        )
        with mock.patch.object(client, "_request", return_value=(200, {}, body)):
            with self.assertRaises(McpError) as ctx:
                client.call_tool("query-meals", {})
        self.assertIn("门店不存在", str(ctx.exception))


class UnwrapTest(unittest.TestCase):
    def test_text_json_is_parsed(self):
        result = {"content": [{"type": "text", "text": '{"data":[1,2]}'}]}
        self.assertEqual(_unwrap_content(result, "t"), {"data": [1, 2]})

    def test_plain_text_is_wrapped(self):
        result = {"content": [{"type": "text", "text": "ok"}]}
        self.assertEqual(_unwrap_content(result, "t"), {"tool": "t", "text": "ok"})

    def test_empty_content_returns_result(self):
        self.assertEqual(_unwrap_content({}, "t"), {})

    def test_summarize_handles_missing_content(self):
        self.assertEqual(_summarize_content({}), "无详细错误信息")


if __name__ == "__main__":
    unittest.main()
