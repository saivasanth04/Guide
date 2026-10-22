"""Tool decisions execute after validated completions, with bounded local history."""

import json
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

from ntg_llm import Agent, Answer, LLMClient, LLMError, create_coder_agent
from ntg_llm.service import CompletionService
from ntg_llm.store import RotationStore
from ntg_llm.tools.filesystem import create_filesystem_registry
from tests.helpers import FakeRouter, deployments


def action(name, **arguments):
    return {"action": "tool", "tool": {"name": name, "arguments": arguments}, "output": None}


def final(output=None):
    return {
        "action": "final",
        "tool": None,
        "output": output or {"code": "pass", "explanation": "Finished."},
    }


def stack(tmp_path, steps):
    root = tmp_path / "workspace"
    root.mkdir()

    class ScriptedRouter(FakeRouter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.histories = []

        def completion(self, **kwargs):
            step = steps[len(self.calls)]
            self.histories.append(deepcopy(kwargs["messages"]))
            self.error = step if isinstance(step, Exception) else None
            self.content = (
                step if isinstance(step, str) else json.dumps(step) if not self.error else ""
            )
            return super().completion(**kwargs)

    service = CompletionService(
        deployments(), RotationStore(tmp_path / "state.db"), ScriptedRouter, sleeper=lambda _: None
    )
    return (
        root,
        service,
        create_coder_agent(LLMClient(service=service), tools=create_filesystem_registry(root)),
    )


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
def test_write_list_read_and_final_preserve_feedback_and_safe_traces(tmp_path, mode):
    private = "private file content"
    root, service, agent = stack(
        tmp_path,
        [
            action("write_file", path="module.py", content=private, overwrite=False),
            action("list_files", path="."),
            action("read_file", path="module.py"),
            final(),
        ],
    )
    for deployment in service.deployments:
        deployment.response_mode = mode
    result = agent.run("Implement the request", "Task context")
    assert (root / "module.py").read_text() == private
    assert result.output.code == "pass"
    assert result.diagnostics["attempt_count"] == 4
    assert [entry["tool"] for entry in result.diagnostics["tool_calls"]] == [
        "write_file",
        "list_files",
        "read_file",
    ]
    assert [entry["turn_number"] for entry in result.diagnostics["attempts"]] == [1, 2, 3, 4]
    assert len(result.diagnostics["agent_steps"]) == 4
    assert private not in json.dumps(result.diagnostics)
    assert "mock-secret" not in result.model_dump_json()
    histories = service.router.histories
    observation = json.loads(histories[1][-1]["content"])
    assert histories[1][-1]["role"] == "user"
    assert observation["type"] == "tool_result"
    assert observation["result"]["data"]["created"] is True
    assert json.loads(histories[3][-1]["content"])["result"]["data"]["content"] == private


def test_expected_tool_failure_is_observed_and_can_be_corrected(tmp_path):
    _, service, agent = stack(tmp_path, [action("read_file", path="missing"), final()])
    result = agent.run("Read a file")
    assert result.diagnostics["tool_calls"][0]["error_code"] == "not_found"
    assert json.loads(service.router.histories[1][-1]["content"])["result"]["ok"] is False


def test_action_budget_never_executes_the_over_budget_request(tmp_path):
    root, service, _ = stack(
        tmp_path,
        [
            action("write_file", path="one", content="1", overwrite=False),
            action("write_file", path="two", content="2", overwrite=False),
        ],
    )
    agent = create_coder_agent(
        LLMClient(service=service), tools=create_filesystem_registry(root), max_tool_calls=1
    )
    with pytest.raises(LLMError) as error:
        agent.run("Create files")
    assert error.value.status_code == 508
    assert error.value.diagnostics["code"] == "tool_limit_reached"
    assert len(service.router.calls) == 2
    assert (root / "one").exists() and not (root / "two").exists()
    assert len(error.value.diagnostics["tool_calls"]) == 1


@pytest.mark.parametrize(
    "failure",
    [
        "not JSON",
        '{"action":"tool","tool":{"name":"unknown","arguments":{}},"output":null}',
    ],
)
def test_malformed_decisions_have_no_effect(tmp_path, failure):
    root, service, agent = stack(tmp_path, [failure])
    with pytest.raises(LLMError) as error:
        agent.run("Do something")
    assert error.value.status_code == 502
    assert list(root.iterdir()) == []
    assert len(service.router.calls) == 1
    assert error.value.diagnostics["tool_calls"] == []


def test_provider_retries_do_not_replay_previous_write(tmp_path):
    class Unavailable(Exception):
        status_code = 503

    root, service, agent = stack(
        tmp_path,
        [
            action("write_file", path="one", content="written", overwrite=False),
            Unavailable("mock-secret"),
            final(),
        ],
    )
    result = agent.run("Create a file")
    assert (root / "one").read_text() == "written"
    assert len(result.diagnostics["tool_calls"]) == 1
    assert result.diagnostics["attempt_count"] == 3
    assert [item["status"] for item in result.diagnostics["failed_attempts"]] == [503]
    assert service.router.histories[1] == service.router.histories[2]


def test_provider_failure_retains_completed_actions_and_timing(tmp_path):
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
    assert error.value.diagnostics["tool_calls"][0]["ok"] is True
    assert error.value.diagnostics["tool_calls"][0]["path"] == "one"
    assert error.value.diagnostics["attempt_count"] == 2
    assert "private" not in json.dumps(error.value.as_dict())


def test_generic_final_contract_works_with_the_same_tools(tmp_path):
    root, service, _ = stack(tmp_path, [final({"answer": "done"})])
    result = Agent(
        "helper",
        "Help",
        LLMClient(service=service),
        tools=create_filesystem_registry(root),
        response_model=Answer,
    ).run("Finish")
    assert result.output.answer == "done"
    assert result.diagnostics["tool_calls"] == []


def test_final_turn_is_allowed_after_the_last_action(tmp_path):
    root, service, _ = stack(tmp_path, [action("list_files", path="."), final()])
    agent = create_coder_agent(
        LLMClient(service=service), tools=create_filesystem_registry(root), max_tool_calls=1
    )
    assert agent.run("Finish").output.code == "pass"
    assert len(service.router.calls) == 2


def test_empty_registry_keeps_the_single_call_interface(tmp_path):
    from ntg_llm.tools import ToolRegistry

    root, service, _ = stack(tmp_path, [{"answer": "done"}])
    result = Agent("helper", "Help", LLMClient(service=service), tools=ToolRegistry([])).run(
        "Finish"
    )
    assert result.output.answer == "done"
    assert len(service.router.calls) == 1
    assert "tool_calls" not in result.diagnostics
    assert list(root.iterdir()) == []


def test_unexpected_handler_failure_reports_uncertain_effects_without_leaks(tmp_path):
    from ntg_llm.tools import ToolRegistry, ToolSpec
    from ntg_llm.tools.filesystem import PathArguments

    root, service, _ = stack(tmp_path, [action("custom", path="one")])

    def handler(args):
        (root / args.path).write_text("private", encoding="utf-8")
        raise RuntimeError("private exception details")

    tools = ToolRegistry(
        [ToolSpec("custom", "A host-owned test handler", PathArguments, Answer, handler)]
    )
    agent = Agent("helper", "Help", LLMClient(service=service), tools=tools)
    with pytest.raises(LLMError) as error:
        agent.run("Perform an action")
    assert error.value.status_code == 500
    assert error.value.diagnostics["tool_calls"][0]["effects_may_have_occurred"] is True
    assert (root / "one").read_text() == "private"
    assert "private" not in json.dumps(error.value.as_dict())


@pytest.mark.parametrize("budget", [0, -1, True, "8"])
def test_invalid_budget_fails_before_any_client_or_action(budget):
    with pytest.raises(LLMError) as error:
        Agent("helper", "Help", LLMClient(), max_tool_calls=budget)
    assert error.value.status_code == 400


def test_concurrent_runs_do_not_share_history_or_call_counters(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()

    class PerTaskRouter(FakeRouter):
        def completion(self, **kwargs):
            messages = kwargs["messages"]
            task = json.loads(messages[1]["content"])["task"]
            if len(messages) == 2:
                decision = action("write_file", path=task, content=task, overwrite=False)
            else:
                observation = json.loads(messages[-1]["content"])["result"]
                assert observation["data"]["path"] == task
                decision = final()
            self.content = json.dumps(decision)
            # Avoid a shared mutable response field crossing thread boundaries.
            selected = self.strategy.get_available_deployment(**kwargs)
            self.calls.append(selected["model_info"]["id"])
            from types import SimpleNamespace as NS

            return NS(choices=[NS(message=NS(content=json.dumps(decision)))])

    service = CompletionService(deployments(), RotationStore(tmp_path / "state.db"), PerTaskRouter)
    agent = create_coder_agent(LLMClient(service=service), tools=create_filesystem_registry(root))
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(agent.run, [f"file{i}" for i in range(8)]))
    for i, result in enumerate(results):
        assert (root / f"file{i}").read_text() == f"file{i}"
        assert result.diagnostics["tool_calls"][0]["call_id"] == "tool_001"
        assert len(result.diagnostics["agent_steps"]) == 2


def react_stack(tmp_path, choose, *, mode="json_schema", max_tool_calls=8):
    """Use observation-dependent fake decisions through the complete production stack."""
    root = tmp_path / "workspace"
    root.mkdir()

    class ReactiveRouter(FakeRouter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.histories = []

        def completion(self, **kwargs):
            history = deepcopy(kwargs["messages"])
            if kwargs["response_format"]["type"] == "json_object":
                history = history[1:]  # Provider-only schema instruction.
            self.histories.append(history)
            self.content = json.dumps(choose(history))
            return super().completion(**kwargs)

    configured = deployments()
    for deployment in configured:
        deployment.response_mode = mode
    service = CompletionService(configured, RotationStore(tmp_path / "state.db"), ReactiveRouter)
    agent = create_coder_agent(
        LLMClient(service=service),
        tools=create_filesystem_registry(root),
        max_tool_calls=max_tool_calls,
    )
    return root, service, agent


@pytest.mark.parametrize("mode", ["json_schema", "json_object"])
@pytest.mark.parametrize("recover", [False, True])
def test_react_observations_drive_recovery_actions_and_final_output(tmp_path, mode, recover):
    source_name = f"input-{uuid4().hex}.txt"
    content = f"observed-only-{uuid4().hex}-\u03bb"

    def choose(history):
        if len(history) == 2:
            return action("read_file", path="missing.txt" if recover else "request.txt")
        assert history[-2]["role"] == "assistant"
        previous = json.loads(history[-2]["content"])
        envelope = json.loads(history[-1]["content"])
        assert history[-1]["role"] == "user" and envelope["type"] == "tool_result"
        observation = envelope["result"]
        assert previous["tool"]["name"] == observation["tool"]
        assert envelope["remaining_tool_calls"] == 8 - (len(history) - 2) // 2
        if not observation["ok"]:
            assert observation["error"]["code"] == "not_found"
            return action("list_files", path=".")
        data = observation["data"]
        if observation["tool"] == "list_files":
            candidates = [entry["name"] for entry in data["entries"] if entry["type"] == "file"]
            assert len(candidates) == 1
            return action("read_file", path=candidates[0])
        if observation["tool"] == "read_file":
            if data["path"] == "request.txt":
                return action("read_file", path=data["content"])
            return action("write_file", path="result.txt", content=data["content"], overwrite=False)
        assert observation["tool"] == "write_file" and data["created"]
        return final({"code": "pass", "explanation": f"Confirmed {data['bytes_written']} bytes."})

    root, service, agent = react_stack(tmp_path, choose, mode=mode)
    (root / source_name).write_text(content, encoding="utf-8")
    if not recover:
        (root / "request.txt").write_text(source_name, encoding="utf-8")
    result = agent.run("Find the input and copy its contents to result.txt")
    assert (root / "result.txt").read_text(encoding="utf-8") == content
    assert result.output.explanation == f"Confirmed {len(content.encode('utf-8'))} bytes."
    calls = result.diagnostics["tool_calls"]
    expected = (
        ["read_file", "list_files", "read_file", "write_file"]
        if recover
        else ["read_file", "read_file", "write_file"]
    )
    assert [entry["tool"] for entry in calls] == expected
    assert [entry["call_id"] for entry in calls] == [
        f"tool_{i:03d}" for i in range(1, len(calls) + 1)
    ]
    assert len(service.router.calls) == len(calls) + 1
    assert len(result.diagnostics["agent_steps"]) == len(calls) + 1
    assert content not in json.dumps(result.diagnostics, ensure_ascii=False)


@pytest.mark.parametrize("actions_needed", [0, 1, 3])
def test_react_finishes_on_model_decision_and_sequential_runs_reset(tmp_path, actions_needed):
    def choose(history):
        task = json.loads(history[1]["content"])["task"]
        assert all(
            other not in json.dumps(history)
            for other in ("first-task", "second-task")
            if other != task
        )
        if len(history) == 2:
            assert "ReAct-style" in history[0]["content"]
            assert "at most 3 tool attempts" in history[0]["content"]
            remaining = 3
        else:
            envelope = json.loads(history[-1]["content"])
            assert envelope["result"]["ok"]
            remaining = envelope["remaining_tool_calls"]
        if 3 - remaining == actions_needed:
            return final({"code": "pass", "explanation": f"Finished {task}."})
        return action("list_files", path=".")

    _, service, agent = react_stack(tmp_path, choose, max_tool_calls=3)
    for task in ("first-task", "second-task"):
        result = agent.run(task)
        assert result.output.explanation == f"Finished {task}."
        assert len(result.diagnostics["tool_calls"]) == actions_needed
        assert len(result.diagnostics["agent_steps"]) == actions_needed + 1
        if actions_needed:
            assert result.diagnostics["tool_calls"][0]["call_id"] == "tool_001"
    assert len(service.router.calls) == 2 * (actions_needed + 1)


def test_react_expected_failure_consumes_budget_and_allows_honest_final(tmp_path):
    def choose(history):
        if len(history) == 2:
            return action("read_file", path="missing.txt")
        envelope = json.loads(history[-1]["content"])
        assert envelope["remaining_tool_calls"] == 0
        assert envelope["result"]["error"]["code"] == "not_found"
        return final(
            {"code": "pass", "explanation": "Incomplete: missing input; no actions remain."}
        )

    root, service, agent = react_stack(tmp_path, choose, max_tool_calls=1)
    result = agent.run("Read the input")
    assert result.output.explanation.startswith("Incomplete:")
    assert list(root.iterdir()) == []
    assert len(service.router.calls) == 2
    assert result.diagnostics["tool_calls"][0]["ok"] is False
