# Milestone 5 — Agents with workspace tools

Historical lifecycle record: the custom decision runtime described below was
replaced by [LangChain ReAct in Milestone 6](milestone6.md). The workspace policy
and registered filesystem handlers remain current. Use Milestone 6 for the
current action protocol and iteration-limit semantics.

The Agent can select a registered tool, supply typed arguments, observe its real
result, and continue until it returns structured output. Tools are optional; the
existing one-call, source-only Coder remains available when none are supplied.

## Enable filesystem actions

Create an existing local directory for model-visible data, then pass it explicitly:

```powershell
New-Item -ItemType Directory -Force .\agent_workspace
python main.py "Create a Python module that normalizes whitespace" --workspace .\agent_workspace
```

The model can use three tools within that directory:

| Tool | Arguments | Result |
|---|---|---|
| `read_file` | `path` | UTF-8 text, relative path, byte count |
| `write_file` | `path`, `content`, `overwrite` | Relative path, bytes written, created/replaced flag |
| `list_files` | `path` (`.` lists the root) | Sorted immediate names/types, explicit truncation flag |

To permit replacing existing files:

```powershell
python main.py "Update the Python modules to match the supplied requirements" --workspace .\agent_workspace --allow-overwrite --context "Your requirements here"
```

Replacement requires both host permission and `overwrite=true` in the tool call.
Creating a new file does not require overwrite permission. Writes can create
missing parent directories; no delete or directory-management tool is exposed.

`--context` remains caller-supplied text. `--max-tool-calls N` sets the maximum
number of action attempts (default 8). Without `--workspace`, no tools are enabled.
`--allow-overwrite` without a workspace is a usage error. Help and usage do not
initialize a client or perform tool I/O. The workspace must already exist; the CLI
does not implicitly grant access to its current working directory.

Normal CLI runs use the configured provider keys. Tests and the implementation
verification use only mocked provider responses and temporary state.

## Python interface

```python
from ntg_llm import LLMClient, create_coder_agent, create_filesystem_registry

tools = create_filesystem_registry("agent_workspace", allow_overwrite=False)
coder = create_coder_agent(LLMClient(), tools=tools, max_tool_calls=8)
result = coder.run("Inspect the workspace and implement the requested Python helper")
print(result.output.code)
print(result.model_dump_json(indent=2))
```

The generic `Agent(..., tools=registry, response_model=YourModel)` uses the same
runtime with any supported final output contract. Its tool support is independent
of Coder instructions or particular filenames. An empty registry behaves like no
tools. Configuration and registries are immutable; history, counters, observations,
and call IDs belong to the current run.

For host-defined capabilities, import `ToolSpec`, `ToolRegistry`, `ToolError`, and
`ToolResult` from `ntg_llm.tools`. A specification binds a name and description to
named Pydantic input/output models and a Python handler that receives the validated
input model. The registry never discovers handlers from model-supplied code or
imports. Custom handlers are trusted host code; filesystem restrictions belong to
the supplied filesystem handlers, not an OS sandbox around arbitrary Python.

## Decision and execution lifecycle

```text
Task/context + tool catalog
    -> model selects a validated tool or final output
    -> registry validates the name and arguments
    -> host executes the handler once
    -> structured observation returns to the model
    -> next decision or final typed output
```

Each decision contains `action`, nullable `tool`, and nullable `output`. Validation
allows exactly one tool request or a final result. Tool arguments use a closed
schema generated from each registered input model; there is no arbitrary argument
dictionary. All fields in the strict schema must be supplied, including the write
request's `overwrite` field.

The loop uses the existing provider-neutral JSON interface. Tool observations are
user-message data, never system instructions. Native provider function-calling
messages and an additional SDK are not required. JSON-object provider mode receives
the schema in a system instruction and still undergoes the same local validation.
Actual live schema support remains provider-specific.

Expected tool failures, such as not_found, path_denied, or already_exists, return
typed error observations. The model can respond with another decision within its
budget. Invalid model JSON, invalid decision branches, and unsupported tool names
fail before their actions are executed. No automatic output repair is attempted.

With budget N, the run performs at most N actions and N+1 completion invocations.
The final turn can finish after the last action. If it requests another action,
the run returns status 508 with `tool_limit_reached` and executes nothing further.
These are execution bounds, not provider quota assumptions.

## Workspace policy

- Only relative paths inside the bound canonical root are allowed. Reject path
  traversal, absolute/drive-relative/UNC/device paths, alternate streams, reserved
  Windows names, control characters, wildcards, and trailing spaces/dots.
- Reject symlinks, reparse points/junctions within the root, hard-linked file
  targets, and nonregular file targets. Network/device roots and protected
  credential roots are not accepted.
- Protect `.env*`, `beatapi.txt`, SQLite state files, private credential/runtime
  directories, and internal staging names. Listings hide protected/link entries.
  A dedicated workspace should contain only data intended for the model; filenames
  cannot identify every secret in arbitrary files.
- Default payload bounds are 262,144 bytes per read/write and 1,000 entries per
  nonrecursive listing. Oversized file operations fail rather than return partial
  content. Listings set `truncated=true` when entries are omitted. Host code can
  configure these values through `create_filesystem_registry`.
- New files are staged and published without replacing an existing destination.
  Authorized replacements use atomic replacement. Publication failure removes the
  staging file. Operations on a shared filesystem-handler instance use a lock;
  exclusive creation also protects against conflicting creates by other writers.
- A failed write can leave newly created parent directories. Multiple writes are
  not one transaction. Concurrent authorized replacements can be last-writer-wins.
  Use a trusted local workspace; this is not isolation from a hostile process
  racing filesystem changes, nor a command/code execution sandbox.

No shell, generated-code execution, file deletion, network tool, repository search,
multi-agent scheduler, or crash-resume mechanism is added. Final `CodeOutput.code`
is syntax-checked and returned; saving a file requires an explicit successful
write action. A final textual claim is not evidence that a file action occurred.

## Diagnostics and partial effects

`AgentResult` keeps `agent_name`, `output`, `model`, and `diagnostics`. In tool mode:

- `attempts`, `failed_attempts`, and `attempt_count` cover every model turn;
  attempt records include `turn_number`.
- `agent_steps` retains each turn's routing diagnostics.
- `tool_calls` records host-assigned call IDs, tool names, status/error codes,
  successful relative paths, and byte/entry counts. It omits file contents and
  complete argument objects. Read contents appear only in the transient model
  observation history, not the execution trace.

The router owns retries/fallback within each completion invocation. A transient
model failure never automatically replays an earlier tool action. Provider failures
keep their status and retry timing; unexpected handler failures stop with a
sanitized 500 and `effects_may_have_occurred`.

A run may write a file and fail in a later turn. Those effects remain on disk;
error diagnostics retain completed-action summaries. The loop does not roll back,
persist/replay actions, or guarantee exactly-once behavior across crashes.

## Offline verification

Tests use real file actions under temporary directories with scripted model
responses and temporary SQLite state. They cover typed dispatch, path protections,
payload limits, overwrite permissions, atomic publication failures, concurrent
actions, feedback history, loop budgets, router retries, partial effects, and CLI
configuration. Existing no-tools, structured-output, provider, rotation, and
persistence tests remain part of the suite.

Verified on 2026-10-22: **195 passed, 1 skipped**. The native-symlink test was
skipped because this Windows host cannot create the link; separate reparse-point
and hard-link protection tests passed. Ruff lint and formatting checks passed.
No live API calls were made.

Run `python -m pytest -q`, `python -m ruff check .`, and
`python -m ruff format --check .`. No live API calls are required.

The [feasibility assessment](milestone5-feasibility.md) and
[implementation plan](../plan/feature-tools-milestone5-v1.md) record the design.
