"""麦当劳 MCP 客户端（Streamable HTTP / JSON-RPC 2.0）。

只依赖标准库。设计要点：

1. 协议完整性：initialize → notifications/initialized → tools/list → tools/call，
   并沿用服务端下发的 ``Mcp-Session-Id``。
2. 容错：服务端既可能返回 ``application/json``，也可能返回 ``text/event-stream``
   （SSE），两种都要能解析。
3. 安全：Token 只从环境变量读取，不写入日志、不打印、不落盘；错误信息里
   如出现 Token 会被脱敏。
4. 限流：官方文档写明 600 次/分钟，命中 429 时按 Retry-After 退避重试。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from typing import Any, Iterable

DEFAULT_URL = "https://mcp.mcd.cn"
PROTOCOL_VERSION = "2025-06-18"
CLIENT_NAME = "mcd-just-enough"
CLIENT_VERSION = "1.0.0"


class McpError(RuntimeError):
    """MCP 调用失败的统一异常。"""

    def __init__(self, message: str, *, status: int | None = None, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _redact(text: str, secret: str | None) -> str:
    if not secret:
        return text
    return text.replace(secret, "***REDACTED***")


def _iter_sse_payloads(body: str) -> Iterable[dict[str, Any]]:
    """从 SSE 文本里逐个取出 JSON 载荷。

    兼容 ``data:`` / ``data: `` 两种写法，以及一条消息跨多行 data 的情况。
    """
    buffer: list[str] = []
    for raw_line in body.splitlines():
        line = raw_line.rstrip("\r")
        if not line:
            if buffer:
                chunk = "\n".join(buffer).strip()
                buffer.clear()
                if chunk and chunk != "[DONE]":
                    try:
                        yield json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            buffer.append(line[5:].lstrip())
    if buffer:
        chunk = "\n".join(buffer).strip()
        if chunk and chunk != "[DONE]":
            try:
                yield json.loads(chunk)
            except json.JSONDecodeError:
                pass


class McpClient:
    """最小可用的麦当劳 MCP 客户端。"""

    def __init__(
        self,
        token: str | None = None,
        url: str | None = None,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        verbose: bool = False,
    ) -> None:
        self.token = token or os.environ.get("MCD_MCP_TOKEN", "").strip()
        if not self.token:
            raise McpError(
                "未找到 MCD_MCP_TOKEN。请先在 https://open.mcd.cn/mcp 激活并复制 MCP Token，"
                "然后 export MCD_MCP_TOKEN=... （或使用 --demo 跑离线示例）"
            )
        self.url = (url or os.environ.get("MCD_MCP_URL", DEFAULT_URL)).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.verbose = verbose
        self._session_id: str | None = None
        self._next_id = 0
        self._initialized = False
        self._tools: list[dict[str, Any]] | None = None

    # ---------------------------------------------------------------- transport

    def _request(self, payload: dict[str, Any]) -> tuple[int, dict[str, str], str]:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": f"{CLIENT_NAME}/{CLIENT_VERSION}",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        req = urllib.request.Request(self.url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers.items()), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read().decode("utf-8", "replace")
        except urllib.error.URLError as exc:
            raise McpError(f"无法连接麦当劳 MCP（{self.url}）：{exc.reason}") from exc

    def _rpc(self, method: str, params: dict[str, Any] | None = None) -> Any:
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            payload["params"] = params

        last_error = "未知错误"
        for attempt in range(self.max_retries):
            status, headers, body = self._request(payload)
            lowered = {k.lower(): v for k, v in headers.items()}
            session_id = lowered.get("mcp-session-id")
            if session_id:
                self._session_id = session_id

            if status == 429:
                wait = float(lowered.get("retry-after", "2") or 2)
                last_error = "触发限流（429），官方限制 600 次/分钟"
                if self.verbose:
                    print(f"[mcp] {last_error}，{wait:.1f}s 后重试（{attempt + 1}/{self.max_retries}）")
                time.sleep(min(wait, 10.0))
                continue
            if status == 401:
                raise McpError(
                    "MCP Token 无效或已过期（401）。请到 https://open.mcd.cn/mcp 重新激活并更新 MCD_MCP_TOKEN。",
                    status=401,
                )
            if status >= 500:
                last_error = f"服务端错误（HTTP {status}）"
                time.sleep(1.5 * (attempt + 1))
                continue

            message = self._decode_body(body, request_id)
            if message is None:
                last_error = f"HTTP {status}，响应无法解析：{_redact(body[:200], self.token)}"
                continue
            if "error" in message:
                err = message["error"] or {}
                raise McpError(
                    f"MCP 返回错误：{err.get('message', err)}", code=err.get("code")
                )
            return message.get("result")

        raise McpError(_redact(last_error, self.token))

    @staticmethod
    def _decode_body(body: str, request_id: int) -> dict[str, Any] | None:
        body = body.strip()
        if not body:
            return None
        if body.startswith("{") or body.startswith("["):
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return None
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict) and item.get("id") == request_id:
                        return item
                return None
            return parsed if isinstance(parsed, dict) else None
        # SSE：取与本次请求 id 匹配的响应，兼容不带 id 的通知
        fallback: dict[str, Any] | None = None
        for item in _iter_sse_payloads(body):
            if item.get("id") == request_id:
                return item
            if "result" in item or "error" in item:
                fallback = item
        return fallback

    # ------------------------------------------------------------------ protocol

    def initialize(self) -> dict[str, Any]:
        if self._initialized:
            return {"already": True}
        result = self._rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        # notifications/initialized 是通知，无 id，失败不影响后续调用
        try:
            self._request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except McpError:
            pass
        self._initialized = True
        return result or {}

    def list_tools(self, *, refresh: bool = False) -> list[dict[str, Any]]:
        if self._tools is not None and not refresh:
            return self._tools
        self.initialize()
        result = self._rpc("tools/list", {}) or {}
        tools = result.get("tools") or []
        if not isinstance(tools, list):
            raise McpError("tools/list 返回结构异常：缺少 tools 数组")
        self._tools = tools
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments or {}}) or {}
        if result.get("isError"):
            raise McpError(f"工具 {name} 返回错误：{_summarize_content(result)}")
        return _unwrap_content(result, name)

    def close(self) -> None:
        self._session_id = None
        self._initialized = False


def _summarize_content(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", ""))[:300])
    return " ".join(parts) or "无详细错误信息"


def _unwrap_content(result: dict[str, Any], tool: str) -> Any:
    """把 MCP 的 content 数组还原成业务数据。

    服务端通常把业务 JSON 序列化进 ``content[0].text``，但也有直接返回结构化
    content 的情况；两种都做兼容。
    """
    content = result.get("content")
    if not content:
        return result.get("structuredContent", result)
    texts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            texts.append(str(item.get("text", "")))
    if not texts:
        return content
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        return {"tool": tool, "text": joined}


_JSON_HINT = re.compile(r"[\[{]")


def looks_like_json(text: str) -> bool:
    return bool(_JSON_HINT.search(text or ""))
