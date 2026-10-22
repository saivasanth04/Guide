# Milestone 6 — Structured ReAct agent loop

The tool-enabled Agent uses a ReAct-style cycle: assess the task and evidence,
select one action, observe its actual result, then reassess until it returns a
typed final answer. The model chooses the trajectory and when to finish.

```text
Task + context + registered tools
    -> private progress assessment
    -> validated tool request -> execute once -> observation
              ^                                  |
              +---------- reassess ---------------+
    -> validated final answer when ready
```

Milestone 5 supplied the tool registry, validated decisions, and observation
transport. Milestone 6 adds an explicit ReAct policy, a completion-driven runtime,
remaining-budget feedback, and acceptance tests that derive subsequent actions
from observed data rather than merely advancing a scripted sequence.

The [original ReAct paper](https://arxiv.org/abs/2210.03629) interleaves reasoning
traces, actions, and observations. This project uses a structured adaptation:
reasoning stays private, while actions and final output use the existing JSON
schema. It does not reproduce the paper's explicit reasoning traces or benchmarks.
Prompting specifies intended model behavior; offline tests cannot establish that
a live model actually reasons well or reliably completes arbitrary tasks.

## Run the agent

Create a directory containing only files intended for the model, then enable tools:

```powershell
New-Item -ItemType Directory -Force .\agent_workspace
python main.py "Inspect the workspace and create a Python whitespace-normalization helper" --workspace .\agent_workspace --max-tool-calls 8
```

```python
from pathlib import Path
from ntg_llm import LLMClient, create_coder_agent, create_filesystem_registry

workspace = Path("agent_workspace")
workspace.mkdir(exist_ok=True)
coder = create_coder_agent(
    LLMClient(),
    tools=create_filesystem_registry(workspace),
    max_tool_calls=8,
)
result = coder.run("Inspect existing files and implement a Python helper")
print(result.output.code)
print(result.output.explanation)
print(result.diagnostics["tool_calls"])
```

Normal runs call configured providers. No-tools runs retain the existing
single-completion behavior. Existing `Agent`, `AgentResult`, Coder output, and CLI
interfaces remain compatible. See [Milestone 5](milestone5.md) for filesystem
permissions, overwrite policy, payload bounds, and custom tool registration.

## ReAct and termination

Each decision has `action`, nullable `tool`, and nullable `output`. A tool decision
contains one registered name and typed arguments; a final decision contains the
validated output contract. The host executes only accepted tool decisions.

The system instruction asks the model to assess evidence privately, use tools
when useful, revise its choice after observations or errors, and finish when it
has enough evidence. The runtime retains each assistant decision and its result
in local history. Observations use this envelope:

```json
{
  "type": "tool_result",
  "result": {
    "call_id": "tool_001",
    "tool": "read_file",
    "ok": false,
    "data": null,
    "error": {
      "code": "not_found",
      "message": "The requested workspace entry does not exist"
    }
  },
  "remaining_tool_calls": 7
}
```

The remaining count comes from the host, outside the tool's result data. For
example, after a missing-file observation the model can list the directory,
choose a discovered file, and read it before deciding how to proceed. The host
does not preselect that recovery sequence.

`max_tool_calls` is an execution ceiling, not a requested action count. With N,
the model may finish immediately or after any of at most N tool attempts. Failed
attempts count too. After the last attempt, one more decision can return final
output, including an honest explanation of incomplete work. Another tool request
is rejected with status 508 and `tool_limit_reached` before it executes. Thus
there are at most N+1 model decision invocations; provider retries can add HTTP
attempts within an invocation. ReAct itself does not guarantee termination.

Expected tool errors are observations. Malformed model decisions, provider
failures after router recovery, and unexpected handler failures terminate with
sanitized errors. Completed writes remain on disk if a later turn fails; action
summaries remain in diagnostics. Provider retries never automatically replay
prior tool actions. Tool results are untrusted user-message data, not system
instructions. There is no native function-call transport or free-text action
parser.

The final output is the model's completion signal, not independent verification
of correctness. The Python Coder checks final source syntax; it cannot execute or
test generated code with the current tool set. No reasoning transcript or file
contents are added to diagnostics. Histories and action IDs reset on each run.

## Verification

New tests traverse the real client, service, schema validation, registry, and
temporary filesystem using an observation-aware fake provider. They cover:

- Reading a filename from an observation, copying unique observed content, and
  deriving the final explanation from the confirmed write result in both response modes.
- Recovering from a missing input through observed directory entries and contents.
- Completion after zero, one, or three actions with the same ceiling of three.
- Sequential reuse of one Agent with isolated histories and reset call IDs.
- A failed last action, zero remaining budget, and an honest incomplete final answer.

Existing coverage checks over-budget rejection, malformed decisions, safe traces,
provider retries without action replay, partial effects, and concurrent isolation.
All tests block live HTTP and use temporary state. Live provider competence and
provider-specific schema support have not been verified by these tests.

Run `python -m pytest -q`, `python -m ruff check .`,
`python -m ruff format --check .`, and `python main.py --help`.

Verified on 2026-10-22: **203 passed, 1 skipped** in 59.52 seconds. The focused
tool-agent suite passed all 25 tests. Ruff lint and formatting checks passed;
CLI help passed and describes the tool-attempt ceiling. The native-symlink test
was skipped because this Windows host cannot create the link; separate hard-link
and reparse-point checks passed. No live provider calls were made.

See the [feasibility study](milestone6-feasibility.md) and
[implementation plan](../plan/feature-agent-loop-milestone6-v1.md).
