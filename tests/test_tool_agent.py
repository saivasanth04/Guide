"""Real LangChain factory/executor with NTG routing and temporary filesystem tools."""

import ast
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from functools import wraps
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from ntg_llm import Agent, Answer, LLMClient, LLMError, create_coder_agent
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from ntg_llm.tools.filesystem import create_filesystem_registry
from tests.helpers import FakeRouter, deployments


def action(name, **arguments):
    text = "Action:\n```json\n" + json.dumps({"action": name, "action_input": arguments}) + "\n```"
    return {"answer": text}


def final(output=None):
    return action("Final Answer", **(output or {"code": "pass", "explanation": "Finished."}))


def observations(history):
    """Read actual LangChain scratchpad observations in the fake provider only."""
    return [
        json.loads(line.removeprefix("Observation: "))
        for line in history[-1]["content"].splitlines()
        if line.startswith("Observation: ")
    ]


def task_from(history):
    return json.loads(history[-1]["content"].splitlines()[0].removeprefix("Question: "))["task"]


def stack(tmp_path, steps=None, *, choose=None, mode="json_schema", limit=8, overwrite=False):
    root = tmp_path / "workspace"
    root.mkdir()

    class ReactiveRouter(FakeRouter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.histories = []

        def completion(self, **kwargs):
            history = deepcopy(kwargs["messages"])
            if kwargs["response_format"]["type"] == "json_object":
                history = history[1:]
            self.histories.append(history)
            step = choose(history) if choose is not None else steps[len(self.calls)]
            self.error = step if isinstance(step, Exception) else None
            self.content = (
                step if isinstance(step, str) else json.dumps(step) if not self.error else ""
            )
            return super().completion(**kwargs)

    configured = deployments()
    for deployment in configured:
        deployment.response_mode = mode
    service = CompletionService(
        configured, RotationStore(tmp_path / "state.db"), ReactiveRouter, sleeper=lambda _: None
    )
    agent = create_coder_agent(
        LLMClient(service=service),
        tools=create_filesystem_registry(root, allow_overwrite=overwrite),
        max_tool_calls=limit,
    )
    return root, service, agent


def test_real_factory_executor_and_tools_own_basic_run(tmp_path, monkeypatch):
    import ntg_llm.tool_runtime as runtime
    from langchain_core.tools import StructuredTool

    invoked, factories, tool_names = [], [], []
    real_factory, real_invoke, real_tool_run = (
        runtime.create_react_agent,
        runtime.AgentExecutor.invoke,
        StructuredTool._run,
    )

    def factory(**kwargs):
        factories.append(kwargs)
        return real_factory(**kwargs)

    def invoke(executor, *args, **kwargs):
        invoked.append(executor)
        return real_invoke(executor, *args, **kwargs)

    @wraps(real_tool_run)
    def tool_run(tool, *args, **kwargs):
        tool_names.append(tool.name)
        return real_tool_run(tool, *args, **kwargs)

    monkeypatch.setattr(runtime, "create_react_agent", factory)
    monkeypatch.setattr(runtime.AgentExecutor, "invoke", invoke)
    monkeypatch.setattr(StructuredTool, "_run", tool_run)
    _, service, agent = stack(tmp_path, [action("list_files", path="."), final()])
    result = agent.run("Inspect the workspace")
    assert len(invoked) == len(factories) == 1
    assert tool_names == ["list_files"]
    assert all(isinstance(tool, StructuredTool) for tool in factories[0]["tools"])
    assert invoked[0].max_iterations == 8 and invoked[0].handle_parsing_errors is False
    assert result.diagnostics["agent_runtime"] == "langchain-classic.AgentExecutor"
    assert result.diagnostics["agent_step_count"] == 2
    assert observations(service.router.histories[1])[0]["tool"] == "list_files"
    assert not hasattr(runtime, "run_with_tools")
    assert not hasattr(agent.tools, "build_decision_model")
    runtime_ast = ast.parse(inspect.getsource(runtime.run_langchain_agent))
    assert not any(
        isinstance(node, (ast.While, ast.For, ast.AsyncFor)) for node in ast.walk(runtime_ast)
    )


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
@pytest.mark.parametrize("recover", [False, True])
def test_observed_directory_and_contents_drive_actions_and_final(tmp_path, mode, recover):
    source_name, content = f"input-{uuid4().hex}.txt", f"private-{uuid4().hex}-\u03bb"

    def choose(history):
        seen = observations(history)
        if not seen:
            return (
                action("read_file", path="missing.txt")
                if recover
                else action("list_files", path=".")
            )
        result = seen[-1]
        if not result["ok"]:
            assert result["error"]["code"] == "not_found"
            return action("list_files", path=".")
        data = result["data"]
        if result["tool"] == "list_files":
            assert len(data["entries"]) == 1
            return action("read_file", path=data["entries"][0]["name"])
        if result["tool"] == "read_file":
            return action("write_file", path="result.txt", content=data["content"], overwrite=False)
        assert result["tool"] == "write_file" and data["created"]
        return final({"code": "pass", "explanation": f"Confirmed {data['bytes_written']} bytes."})

    root, service, agent = stack(tmp_path, choose=choose, mode=mode)
    (root / source_name).write_text(content, encoding="utf-8")
    result = agent.run("Find the input and copy its contents")
    assert (root / "result.txt").read_text(encoding="utf-8") == content
    assert result.output.explanation == f"Confirmed {len(content.encode('utf-8'))} bytes."
    expected = (["read_file"] if recover else []) + ["list_files", "read_file", "write_file"]
    calls = result.diagnostics["tool_calls"]
    assert [item["tool"] for item in calls] == expected
    assert [item["call_id"] for item in calls] == [
        f"tool_{i:03d}" for i in range(1, len(calls) + 1)
    ]
    assert len(service.router.calls) == len(calls) + 1
    assert len(result.diagnostics["agent_steps"]) == len(calls) + 1
    assert content not in json.dumps(result.diagnostics, ensure_ascii=False)
    assert "mock-secret" not in result.model_dump_json()


def test_e2e_greeting_edit_uses_real_langchain_and_router(tmp_path, monkeypatch):
    from ntg_llm.langchain_model import NTGChatModel
    from ntg_llm.tool_runtime import AgentExecutor
    from ntg_llm.tools.filesystem import FilesystemTools

    events = []
    for cls, name, label in (
        (AgentExecutor, "invoke", "executor"),
        (NTGChatModel, "_generate", "model"),
        (FilesystemTools, "write_file", "write"),
    ):
        original = getattr(cls, name)

        def record(self, *args, _original=original, _label=label, **kwargs):
            events.append(_label)
            return _original(self, *args, **kwargs)

        monkeypatch.setattr(cls, name, record)

    def choose(history):
        seen = observations(history)
        if not seen:
            return action("list_files", path=".")
        result = seen[-1]
        data = result["data"]
        if result["tool"] == "list_files":
            return action("read_file", path=data["entries"][0]["name"])
        if result["tool"] == "read_file":
            source = data["content"].replace('"Hello"', '"Hello from ReAct"')
            assert source != data["content"]
            return action("write_file", path=data["path"], content=source, overwrite=True)
        previous_read = next(item for item in seen if item["tool"] == "read_file")
        return final(
            {
                "code": previous_read["data"]["content"].replace('"Hello"', '"Hello from ReAct"'),
                "explanation": f"Modified {data['path']}.",
            }
        )

    root, service, agent = stack(tmp_path, choose=choose, overwrite=True)
    filename = f"greeting-{uuid4().hex}.py"
    (root / filename).write_text('def greeting():\n    return "Hello"\n', encoding="utf-8")
    result = agent.run(
        'Find the greeting function, change it to return "Hello from ReAct", and report the modified file.'
    )
    assert (root / filename).read_text(
        encoding="utf-8"
    ) == 'def greeting():\n    return "Hello from ReAct"\n'
    assert result.output.explanation == f"Modified {filename}."
    assert events == ["executor", "model", "model", "model", "write", "model"]
    assert len(service.router.calls) == 4
    assert [item["turn_number"] for item in result.diagnostics["attempts"]] == [1, 2, 3, 4]


def test_limit_is_owned_by_executor_and_never_executes_extra_action(tmp_path):
    root, service, agent = stack(
        tmp_path,
        [
            action("write_file", path="one", content="1", overwrite=False),
            action("write_file", path="two", content="2", overwrite=False),
        ],
        limit=1,
    )
    with pytest.raises(LLMError) as error:
        agent.run("Create files")
    assert error.value.status_code == 508
    assert error.value.diagnostics["code"] == "tool_limit_reached"
    assert len(service.router.calls) == 1
    assert (root / "one").exists() and not (root / "two").exists()
    assert len(error.value.diagnostics["tool_calls"]) == 1


def test_final_decision_fits_within_executor_limit(tmp_path):
    _, service, agent = stack(tmp_path, [action("list_files", path="."), final()], limit=2)
    assert agent.run("Inspect").output.code == "pass"
    assert len(service.router.calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        "not JSON",
        {"answer": "private invalid action"},
        action("unknown", path="secret"),
        final({"code": "def broken(", "explanation": "private"}),
        final({"code": "pass", "explanation": "ok", "extra": True}),
    ],
)
def test_invalid_model_or_final_output_stops_without_private_text(tmp_path, payload):
    root, service, agent = stack(tmp_path, [payload])
    with pytest.raises(LLMError) as error:
        agent.run("private task")
    assert error.value.status_code == 502
    assert list(root.iterdir()) == []
    assert len(service.router.calls) == 1
    assert "private" not in json.dumps(error.value.as_dict())
    assert error.value.diagnostics["tool_calls"] == []


@pytest.mark.parametrize("arguments", [{}, {"path": 7}, {"path": ".", "extra": "private"}])
def test_typed_tool_invalid_input_is_safe_observation(tmp_path, arguments):
    _, service, agent = stack(tmp_path, [action("list_files", **arguments), final()])
    result = agent.run("Inspect")
    seen = observations(service.router.histories[1])
    assert seen[-1]["error"]["code"] == "invalid_arguments"
    assert result.diagnostics["tool_calls"][0]["error_code"] == "invalid_arguments"
    assert "private" not in json.dumps(result.diagnostics)


def test_provider_retry_does_not_replay_successful_write(tmp_path, monkeypatch):
    from ntg_llm.tools.filesystem import FilesystemTools

    class Unavailable(Exception):
        status_code = 503

    writes = []
    original = FilesystemTools.write_file

    def write(self, args):
        writes.append(args.path)
        return original(self, args)

    monkeypatch.setattr(FilesystemTools, "write_file", write)
    root, service, agent = stack(
        tmp_path,
        [
            action("write_file", path="one", content="written", overwrite=False),
            Unavailable("mock-secret"),
            final(),
        ],
    )
    result = agent.run("Create a file")
    assert writes == ["one"]
    assert (root / "one").read_text() == "written"
    assert len(result.diagnostics["tool_calls"]) == 1
    assert result.diagnostics["attempt_count"] == 3
    assert result.diagnostics["agent_step_count"] == 2
    assert [item["status"] for item in result.diagnostics["failed_attempts"]] == [503]
    assert service.router.histories[1] == service.router.histories[2]


def test_protocol_labels_in_code_do_not_confuse_official_json_parser(tmp_path):
    code = 'def label():\n    return "Final Answer: Observation: Action:"\n'
    root, _, agent = stack(
        tmp_path,
        [
            action("write_file", path="labels.py", content=code, overwrite=False),
            final({"code": code, "explanation": "Wrote the protocol label helper."}),
        ],
    )
    result = agent.run("Write the label helper")
    assert result.output.code == (root / "labels.py").read_text() == code


def test_private_trajectory_is_not_printed_logged_or_uploaded(
    tmp_path, capsys, caplog, monkeypatch
):
    from langsmith import Client, tracing_context

    private = "private-reasoning-marker"
    uploads = []

    def record_upload(*args, **kwargs):
        uploads.append(True)

    monkeypatch.setattr(Client, "create_run", record_upload)
    monkeypatch.setattr(Client, "update_run", record_upload)
    # The official parser accepts the first action in a list and normally logs
    # the complete list. The integration suppresses that unsafe parser warning.
    text = json.dumps(
        [
            {"action": "list_files", "action_input": {"path": "."}},
            {"action": private, "action_input": {}},
        ]
    )
    _, _, agent = stack(tmp_path, [{"answer": text}, final()])
    with tracing_context(enabled=True):
        result = agent.run("Inspect")
    assert uploads == []
    assert len(result.diagnostics["tool_calls"]) == 1
    output = capsys.readouterr()
    assert private not in output.out + output.err + caplog.text + result.model_dump_json()


def test_provider_failure_preserves_partial_effects_retry_after_and_diagnostics(tmp_path):
    class Limited(Exception):
        status_code = 429
        headers = {"Retry-After": "15"}

    root, _, agent = stack(
        tmp_path,
        [
            action("write_file", path="one", content="private-content", overwrite=False),
            Limited("mock-secret"),
        ],
    )
    with pytest.raises(LLMError) as error:
        agent.run("private task", "private context")
    assert error.value.status_code == 429 and error.value.retry_after == "15"
    assert (root / "one").read_text() == "private-content"
    assert error.value.diagnostics["agent_name"] == "coder"
    assert error.value.diagnostics["tool_calls"][0]["path"] == "one"
    assert error.value.diagnostics["attempt_count"] == 2
    assert "private" not in json.dumps(error.value.as_dict())


def test_unexpected_handler_failure_stops_with_uncertain_effects(tmp_path):
    from ntg_llm.tools import ToolRegistry, ToolSpec
    from ntg_llm.tools.filesystem import PathArguments

    root, service, _ = stack(tmp_path, [action("custom", path="one")])

    def handler(args):
        (root / args.path).write_text("private", encoding="utf-8")
        raise RuntimeError("private exception details")

    tools = ToolRegistry(
        [ToolSpec("custom", "A host-owned test handler", PathArguments, Answer, handler)]
    )
    with pytest.raises(LLMError) as error:
        Agent("helper", "Help", LLMClient(service=service), tools=tools).run("Perform an action")
    assert error.value.status_code == 500
    assert error.value.diagnostics["tool_calls"][0]["effects_may_have_occurred"] is True
    assert (root / "one").read_text() == "private"
    assert "private" not in json.dumps(error.value.as_dict())


def test_generic_immediate_final_and_empty_registry_direct_mode(tmp_path):
    from ntg_llm.tools import ToolRegistry

    root, service, _ = stack(tmp_path, [final({"answer": "done"}), {"answer": "direct"}])
    result = Agent(
        "helper",
        "Help",
        LLMClient(service=service),
        tools=create_filesystem_registry(root),
        response_model=Answer,
        max_tool_calls=1,
    ).run("Finish")
    assert result.output.answer == "done"
    assert result.diagnostics["tool_calls"] == []
    direct = Agent("helper", "Help", LLMClient(service=service), tools=ToolRegistry([])).run(
        "Finish"
    )
    assert direct.output.answer == "direct"
    assert "tool_calls" not in direct.diagnostics


@pytest.mark.parametrize("budget", [0, -1, True, "8"])
def test_invalid_budget_fails_before_any_client_or_action(budget):
    with pytest.raises(LLMError) as error:
        Agent("helper", "Help", LLMClient(), max_tool_calls=budget)
    assert error.value.status_code == 400


def test_sequential_and_concurrent_runs_keep_independent_langchain_state(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    class PerTaskRouter(FakeRouter):
        def completion(self, **kwargs):
            history = kwargs["messages"]
            task = task_from(history)
            seen = observations(history)
            if not seen:
                decision = action("write_file", path=task, content=task, overwrite=False)
            else:
                assert len(seen) == 1 and seen[0]["data"]["path"] == task
                decision = final()
            selected = self.strategy.get_available_deployment(**kwargs)
            self.calls.append(selected["model_info"]["id"])
            return NS(choices=[NS(message=NS(content=json.dumps(decision)))])

    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), PerTaskRouter)
    agent = create_coder_agent(LLMClient(service=service), tools=create_filesystem_registry(root))
    first = agent.run("before")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(agent.run, [f"file{i}" for i in range(8)]))
    last = agent.run("after")
    for result in [first, *results, last]:
        assert result.diagnostics["tool_calls"][0]["call_id"] == "tool_001"
        assert result.diagnostics["agent_step_count"] == 2
    for i in range(8):
        assert (root / f"file{i}").read_text() == f"file{i}"
