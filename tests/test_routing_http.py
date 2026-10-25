"""Real HTTP transport through LiteLLM and the public client, without cloud credentials."""

import json
import asyncio
import shutil
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ntg_llm import LLMClient
from ntg_llm.errors import RoutingError
from ntg_llm.schemas import Deployment
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore


@contextmanager
def endpoint(replies=None, *, gemini=False):
    calls = []
    release = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, body))
            if self.path.startswith("/slow"):
                release.wait(5)
            rate_limited = self.path.startswith("/rate")
            payload = (
                {
                    "error": {
                        "message": "Busy",
                        "type": "rate_limit_error",
                        "code": "rate_limit_exceeded",
                    }
                }
                if rate_limited
                else {
                    "id": "local-test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "gpt-4o-mini",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": replies.pop(0) if replies is not None else "HTTP_OK",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            )
            encoded = json.dumps(payload).encode()
            if gemini and not rate_limited:
                payload = {"candidates": [{"content": {"role": "model", "parts": [
                    {"text": payload["choices"][0]["message"]["content"]}
                ]}, "finishReason": "STOP", "index": 0}],
                    "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 1, "totalTokenCount": 2},
                    "modelVersion": "gemini-3.8-flash"}
                encoded = json.dumps(payload).encode()
            try:
                self.send_response(429 if rate_limited else 200)
                if rate_limited:
                    self.send_header("Retry-After", "30")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(2)


def make_service(tmp_path, base, paths, **kwargs):
    deployments = [
        Deployment(
            f"key_{i}",
            None,
            "gpt-4o-mini",
            "local-test-key",
            provider="openai",
            api_base=base + path,
        )
        for i, path in enumerate(paths)
    ]
    return CompletionService(deployments, RotationStore(tmp_path / "http.db"), **kwargs)


def test_public_client_real_http_429_failover_and_cooldown(tmp_path):
    with endpoint() as (base, calls):
        service = make_service(
            tmp_path, base, ["/rate", "/ok"], request_timeout=5, routing_time_budget=10
        )
        client = LLMClient(service=service)
        result = client.models.generate_content(model="router", contents="A task")
        assert result.text == "HTTP_OK"
        assert result.diagnostics["attempt_count"] == 2
        assert result.diagnostics["failed_attempts"][0]["status"] == 429
        assert (
            client.models.generate_content(model="router", contents="Next task").text == "HTTP_OK"
        )
        assert [path for path, _ in calls] == [
            "/rate/chat/completions",
            "/ok/chat/completions",
            "/ok/chat/completions",
        ]


def test_public_client_real_http_stalls_cannot_multiply_latency(tmp_path):
    with endpoint() as (base, calls):
        service = make_service(
            tmp_path,
            base,
            ["/slow/1", "/slow/2", "/slow/3"],
            request_timeout=0.25,
            routing_time_budget=0.40,
        )
        client = LLMClient(service=service)
        start = time.monotonic()
        with pytest.raises(RoutingError) as error:
            client.models.generate_content(model="router", contents="A task")
        elapsed = time.monotonic() - start
        assert 0.35 <= elapsed < 1.2
        assert error.value.status_code == 504
        assert error.value.diagnostics["time_budget_exhausted"]
        assert len(service.router.model_list) == 3
        assert len(error.value.diagnostics["attempts"]) == 2
        assert len(calls) <= 2


def test_real_http_timeout_fails_over_to_healthy_deployment(tmp_path):
    with endpoint() as (base, calls):
        service = make_service(
            tmp_path, base, ["/slow", "/ok"], request_timeout=0.5, routing_time_budget=3
        )
        # Warm LiteLLM's lazy SDK initialization so the short timeout exercises
        # a stalled HTTP response rather than startup before the first socket.
        service._complete_once(
            [{"role": "user", "content": "Warm transport"}],
            service.deployments[1],
            response_model=None,
            timeout=5,
        )
        calls.clear()
        result = LLMClient(service=service).models.generate_content(
            model="router", contents="A task"
        )
        assert result.text == "HTTP_OK"
        assert result.diagnostics["attempt_count"] == 2
        assert result.diagnostics["failed_attempts"][0]["status"] == 408
        assert [path for path, _ in calls] == ["/slow/chat/completions", "/ok/chat/completions"]


@pytest.mark.skipif(not shutil.which("npx"), reason="MCP filesystem server requires npx")
def test_react_router_http_and_real_mcp_filesystem_end_to_end(tmp_path):
    from coder_agent import REACT_PROMPT, filesystem_config
    from langchain_classic.agents import AgentExecutor, create_react_agent
    from langchain_core.runnables import RunnableLambda
    from langchain_mcp_adapters.client import MultiServerMCPClient
    from ntg_llm.langchain_adapter import NTGLangChainLLM
    from ntg_llm.react_parser import parse_react_output, render_mcp_tools

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    destination = workspace / "report.md"
    content = "Integration complete.\n"
    replies = [
        "Action: write_file\nAction Input: "
        + json.dumps({"path": str(destination), "content": content}),
        "Action: read_text_file\nAction Input: " + json.dumps({"path": str(destination)}),
        "Final Answer: Integration complete.",
    ]
    with endpoint(replies) as (base, calls):
        service = make_service(
            tmp_path, base, ["/rate", "/ok"], request_timeout=5, routing_time_budget=10
        )

        async def run():
            tools = await MultiServerMCPClient(filesystem_config(str(workspace))).get_tools()
            agent = create_react_agent(
                NTGLangChainLLM(client=LLMClient(service=service)),
                tools,
                REACT_PROMPT.partial(workspace=str(workspace)),
                output_parser=RunnableLambda(parse_react_output),
                tools_renderer=render_mcp_tools,
            )
            return await AgentExecutor(
                agent=agent, tools=tools, max_iterations=5, return_intermediate_steps=True
            ).ainvoke(
                {
                    "input": f"Write {content!r} to {destination}, then read it and report the content."
                }
            )

        result = asyncio.run(run())
        assert destination.read_text() == content
        assert result["output"] == "Integration complete."
        assert [action.tool for action, _ in result["intermediate_steps"]] == [
            "write_file",
            "read_text_file",
        ]
        assert len(calls) == 4  # one 429, then three successful ReAct turns
        assert not replies
