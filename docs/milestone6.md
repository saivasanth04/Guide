# Milestone 6 — LangChain ReAct agent

Tool-enabled runs use the actual `create_react_agent` and `AgentExecutor` from
`langchain_classic.agents`. LangChain owns the ReAct lifecycle, including action
parsing, tool dispatch, observations, scratchpad construction, and termination.
NTG no longer implements its own action/final state machine or conversation loop.

```text
Coder / Agent.run
    -> LangChain create_react_agent + AgentExecutor.invoke
        -> NTGChatModel -> LLMClient -> CompletionService
            -> LiteLLM Router -> PersistentRoundRobin -> selected provider
        -> LangChain StructuredTool -> ToolRegistry.execute -> FilesystemTools
        -> LangChain scratchpad -> next model decision
    -> strict final JSON validation -> AgentResult[CodeOutput]
```

## API choice and pinned dependencies

Checked on 2026-10-22 using official documentation, PyPI metadata, and installed
source: the environment originally had LangChain 1.3.1, core 1.4.0, classic 1.0.7,
and LangGraph 1.2.0. The latest stable releases returned by PyPI were LangChain
1.4.7, core 1.6.13, classic 1.0.8, and LangGraph 1.2.16.

Modern LangChain recommends `langchain.agents.create_agent` for native tool-calling
models. NTG's current client supports schema-validated text completions, with
system/user/assistant messages, rather than native tool calls. The classic API
provides the requested real ReAct executor while preserving that transport and
every existing provider adapter. It is an older API; its own documentation
recommends modern `create_agent` for new production agents. This choice is a
deliberate compatibility tradeoff, not a claim that classic is the newest API.

Direct dependencies added:

```text
langchain-classic==1.0.8
langchain-core==1.6.13
```

No `langchain-openai` or new provider integration is required. No LangGraph
multi-agent orchestration is added. Transitive official dependencies are resolved
by pip. Install the project with `python -m pip install -e ".[dev]"`.

Sources: [official migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1),
[classic package](https://pypi.org/project/langchain-classic/1.0.8/),
[official factory source](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/agents/react/agent.py),
[official JSON action parser](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/agents/output_parsers/json.py).

## Use the existing public API

```powershell
New-Item -ItemType Directory -Force .\agent_workspace
python main.py "Inspect the files and implement the requested Python helper" --workspace .\agent_workspace --max-tool-calls 8
```

```python
from pathlib import Path
from ntg_llm import LLMClient, create_coder_agent, create_filesystem_registry

workspace = Path("agent_workspace")
workspace.mkdir(exist_ok=True)
coder = create_coder_agent(
    llm=LLMClient(),
    tools=create_filesystem_registry(workspace),
    max_tool_calls=8,
)
result = coder.run("Find the greeting module and update the greeting")
print(result.output.code)
print(result.output.explanation)
print(result.diagnostics["tool_calls"])
```

Replacing existing files still requires both host `allow_overwrite=True` (CLI
`--allow-overwrite`) and a selected write with `overwrite=true`. Normal CLI runs
call configured providers. Tests use fake providers and temporary workspaces.

## Model and tool adapters

`NTGChatModel` implements LangChain's `BaseChatModel._generate`. It converts text
messages, calls the same `LLMClient.chat.completions.create(model="router", ...)`,
and returns a LangChain `AIMessage`. It does not choose providers, keys, accounts,
or models, and adds no retry/fallback policy. Model caching and streaming are
disabled. Provider retries remain inside each NTG completion call.

The existing `Answer` schema is only a transport envelope: `answer` carries ReAct
text for LangChain's parser. A tool action uses the official parser's format:

````text
Action:
```json
{"action":"read_file","action_input":{"path":"module.py"}}
```
````

A final response uses the same official JSON protocol:
`{"action":"Final Answer","action_input":{"code":"...","explanation":"..."}}`.
LangChain's official `JSONAgentOutputParser` parses this text. Its
dictionary `action_input` supports multi-field tools such as `write_file`.
NTG does not implement a Thought/Action parser. The prompt contains `{tools}`,
`{tool_names}`, `{agent_scratchpad}`, and `{input}`; the official factory renders
the scratchpad from its intermediate steps. The prompt requests private progress
assessment and emits no reasoning transcript.

Intermediate actions are not `CodeOutput`. After LangChain returns `AgentFinish`,
NTG strictly validates the final object against the caller's response model,
including extra/missing fields, types, and Coder syntax checks.
Invalid final output returns a sanitized 502 without an output-repair retry.
Using LangChain's JSON action parser also keeps literal strings such as
`Final Answer:` inside Python source from being interpreted as protocol delimiters.
LangChain parses the protocol JSON; its permissive parser can normalize malformed
JSON or select the first entry of a returned list. NTG validates the resulting
object, but does not claim to apply its direct API's duplicate-key detection to
the original inner action text. Its unsafe full-response parser warning is suppressed.

`create_langchain_tools` exposes registry entries as `StructuredTool` instances
with closed JSON argument schemas derived from their Pydantic models. LangChain
dispatches the selected tool; its wrapper delegates to `ToolRegistry.execute`.
The registry validates the original arguments before calling any handler. JSON
schemas prevent LangChain from coercing types or inserting defaults ahead of that
strict host validation. Expected errors become typed JSON observations; unknown
tools stop before dispatch. Existing filesystem protections are unchanged:
workspace scoping, protected credentials/state paths, link restrictions, payload
bounds, and explicit overwrite policy. This is a trusted-workspace boundary, not
an OS sandbox against another process racing filesystem links or arbitrary custom
host handlers. See [filesystem policy](milestone5.md#workspace-policy).

## One execution ceiling

`max_tool_calls=N` maps directly to `AgentExecutor.max_iterations=N`. The selected
official parser returns one action per decision, so at most N tools can execute.
The final decision also occupies an iteration. For three actions plus a final
answer, configure at least four iterations. Failed actions consume iterations.
The model can return an immediate final answer, including with a limit of one.

If the executor exhausts its iterations, its `early_stopping_method="force"`
stops the run; NTG converts that stop into a controlled 508 with
`code="tool_limit_reached"`. No over-limit action is requested or executed.
There is no independent NTG budget counter or automatically added final turn.
This intentionally replaces the old N-tools-plus-one-final-turn behavior.

Parsing recovery and executor-level retries are disabled. A provider retry
retries only the current model completion; already-completed tools cannot replay
through that path. The model can explicitly choose another write as a new action,
which is subject to the usual overwrite policy. There is no crash-resume or
exactly-once guarantee across restarts. Earlier writes remain if a later call
fails; diagnostics retain completed-action summaries and uncertain effects.

## Diagnostics and privacy

`AgentResult` retains `agent_name`, typed `output`, selected `model`, and
`diagnostics`. Per-run evidence includes:

- `agent_runtime`: `langchain-classic.AgentExecutor`.
- `agent_steps` and `agent_step_count`: NTG completion invocations, including final
  decisions and failed model calls, with each invocation's routing diagnostics.
- `attempts`, `failed_attempts`, `attempt_count`: aggregated upstream attempts
  tagged with turn numbers; retries can increase attempts without adding actions.
- `tool_calls`: host-assigned IDs, registered names, status/error codes, safe
  relative paths, and byte/entry counts. Expected failures are recorded too.

Diagnostics omit prompts, file contents, full argument objects, action logs,
scratchpads, and private reasoning. `verbose=False` and disabled LangSmith tracing
prevent the executor from printing or uploading trajectories. Parser exceptions
can contain raw model output; NTG converts these to fixed errors. Provider
`RoutingError` status, retry timing, and recovery flags remain intact.

Runs create fresh executor/model/trace instances and share only immutable
configuration and the existing client/registry. Direct completion APIs and agents
without tools bypass the executor entirely, including lazy framework imports.

## Verification

`tests/test_tool_agent.py` instruments the real factory, executor invocation,
typed tools, model adapter, and real temporary filesystem writes. It verifies
list/read/write/final flow, observation-dependent filenames and content, recovery,
single authoritative bounds, strict final validation, retries without replay,
partial effects, generic output schemas, and concurrent/sequential isolation.
`tests/test_langchain_model.py` checks routing delegation, strict tool arguments,
unsupported model features, and direct APIs without the executor.

The greeting E2E test discovers a uniquely named Python file, reads it, replaces
its greeting with `Hello from ReAct`, writes through the existing handler, and
reports the observed modified path. Assertions verify executor -> model ->
existing router -> filesystem -> scratchpad -> model -> typed final output.
This uses the actual LangChain runtime and a fake provider, not a fake executor.
No live model judgment or universal provider compatibility is claimed.

Run `python -m pytest -q`, `python -m ruff check .`,
`python -m ruff format --check .`, and `python main.py --help`.

Verified on 2026-10-22: the complete suite passed **209 tests, with one skipped**
in 227.89 seconds. Ruff lint/format and CLI help passed. The requested greeting
E2E was then run separately and passed in 96.61 seconds:
`python -m pytest -q tests/test_tool_agent.py::test_e2e_greeting_edit_uses_real_langchain_and_router`.
The native-symlink test is skipped because this Windows host cannot create the
link; separate reparse/hard-link tests passed. Four import warnings originate from
optional CUDA/SWIG/community packages already installed on this machine; those
integrations are not project dependencies. No live provider requests were made.
The privacy test was also rerun separately with spies on LangSmith SDK upload
methods; it passed and recorded no create/update upload calls under enabled
outer tracing. Agent execution overrides that outer tracing context to disabled.

The [feasibility assessment](milestone6-feasibility.md) and
[migration plan](../plan/refactor-langchain-react-v1.md) record the architecture.
