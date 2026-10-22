# Milestone 5 feasibility assessment

Status: assessment completed before implementation. The approved plan is now
implemented; see [Milestone 5](milestone5.md) for current usage and verification.
The assessment below records the original Milestone 4 baseline.

## Goal

Give the existing Agent explicitly registered capabilities that can perform real
actions. The model selects a tool and arguments, the host validates and executes
that selection, and the result returns to the model so it can decide what to do
next or finish with structured output. Merely returning a JSON action or putting
functions in a dictionary does not complete this milestone.

The first tool set will read, create/write, and list UTF-8 workspace files. These
operations fit the requested starting scope and the current single Coder. Tool
registration and execution remain independent of any particular task or filename.
This is an architectural extension to the existing Agent, not a task planner.

## Cross-verification against the repository

| Need | Current evidence | Assessment |
|---|---|---|
| Reusable Agent | `Agent` in `ntg_llm/agent.py` | Ready for optional tool configuration; currently one client invocation per run. |
| Structured model decisions | `response_schema` and `validate_response` in `ntg_llm/schemas.py` | Named object models and nested unions can represent a tool request or final output. Open argument dictionaries are rejected. |
| Provider-independent calls | `LLMClient` in `ntg_llm/client.py` | Reuse the existing JSON completion path for every decision turn. |
| Multi-turn history | `CompletionService.chat_completion` in `ntg_llm/service.py` | Accepts system/user/assistant text messages; suitable for JSON-encoded observations. |
| Native function/tool messages | The same method rejects message roles beyond system/user/assistant; the client has no tools parameter | Not supported today. Native function calling would require a wider client/provider change. |
| Execute registered actions | No tools package or executor exists | Add a registry with typed input/output contracts and explicit handlers. |
| Filesystem policy | No workspace-scoped filesystem module exists | Add a root-bound implementation that validates paths and permissions before I/O. |
| Observe tool results | `Agent.run` currently returns immediately after one completion | Add a bounded tool decision loop with local per-run history. |
| Retry/fallback | Existing router owns upstream recovery and persistent selection | Keep these per model turn; never replay filesystem actions as part of provider retries. |
| Diagnostics | `AgentResult` carries routing diagnostics | Extend diagnostics to include every model turn and safe tool summaries, including partial effects on failure. |

## Offline verification performed

On 2026-10-22, the complete existing suite passed: **109 mocked tests**.

A separate throwaway feasibility probe used the actual `LLMClient`, completion
module, schema validation, fake LiteLLM router, and a temporary SQLite database.
A closed Pydantic decision model contained a union of typed read/write/list calls
and an optional `CodeOutput`. Branch validation required exactly one action.

The probe successfully completed four mocked model turns:

1. A typed write request created a UTF-8 text file in a temporary directory.
2. A typed listing request observed the file.
3. A typed read request retrieved its contents.
4. A final decision returned typed `CodeOutput`.

Actions were executed by a small in-memory dispatcher, and observations were
returned as text messages through the current client. The directory was removed
afterward. No production tool implementation was created, no configured secrets
were loaded, no production SQLite state was changed, and no live API calls ran.

This verifies local integration feasibility. It does not prove that a live
provider accepts every generated schema or that path protections have already
been implemented; those require the implementation tests described in the plan.

## Recommended design

Use optional registered tools on the current Agent, with a bounded structured
decision loop. Preserve the existing one-call behavior when no tools are supplied.

```text
Task + context + registered tool descriptions
    -> structured model decision
        -> final output: validate and return AgentResult
        -> tool request: validate registration and typed arguments
            -> execute once inside the configured workspace
            -> append a structured observation
            -> request the next decision
```

The registry generates each tool-call schema from its input model. For a write,
this means explicit path/content/overwrite fields rather than an open-ended
arguments object. The decision contains `action`, `tool`, and `output`; the latter
two are nullable, and validation permits only a tool request or a final result.

The filesystem handlers bind the host-selected workspace and policy in Python.
The model supplies relative paths, not a new root or new permission settings.
An existing output schema such as `CodeOutput` remains the final output contract;
a tool result is intermediate evidence, not an automatic final answer.

## Scope and policy decisions

- Tools are enabled explicitly by passing a registry or using CLI `--workspace`.
  The workspace must already exist. The CLI's default mode remains Milestone 4.
- First operations: read UTF-8 files, list one directory, and write UTF-8 files.
  Writes create new files by default. Replacing a file requires both host
  `--allow-overwrite` and `overwrite=true` in the selected write request.
- All operations are scoped to the canonical workspace. Reject absolute/drive/
  UNC/device paths, traversal, symlinks/reparse points inside the workspace,
  hard-linked file targets, Windows alternate streams, and known protected paths.
- Protect `.env*`, `beatapi.txt`, SQLite state, and private runtime/credential
  directories even if the operator points the workspace at a project directory.
  A workspace should contain only data intended for the model; filename rules
  cannot identify every possible secret in arbitrary files.
- Use local per-run history and an immutable registry. No global conversation
  history, persistent tool replay queue, or automatic restart/resume is added.
- Default execution budget: eight tool calls and at most nine model decision
  turns. This bounds actions, not provider RPM/TPM/RPD; no quota assumptions return.
- Start with 256 KiB per read/write and 1,000 entries per nonrecursive listing.
  These are explicit filesystem payload bounds, configurable in the host policy.
- No shell, Python execution, deletion, network, repository search, or multi-agent
  orchestration is added. Creating directories is an internal convenience of an
  authorized write; no separate directory-management tool is exposed.

## Alternatives

1. **Structured decision loop — recommended.** Reuses validated JSON and every
   existing provider adapter, with explicit execution under host control.
2. **Native provider function calling.** A future option after extending the
   facade, message normalization, response types, and provider adapter tests.
3. **Tools without a feedback loop.** Insufficient for the milestone because the
   agent cannot observe its actions and reliably decide whether to continue.

## Reliability boundary

Provider retries happen before a decision is returned. Filesystem execution
happens afterward, exactly once per accepted step, without an automatic tool retry.
Expected tool failures become bounded observations the model can respond to.
Malformed model output stops with a sanitized error before any associated action.

A run can make real changes and then fail in a later turn. Those changes are not
rolled back: errors must include completed-action summaries without file contents.
There is no exactly-once guarantee across crashes/restarts. Concurrent creations
must not overwrite one another; explicitly permitted replacements use atomic
publication and can have last-writer-wins behavior.

Path checks operate within a trusted local workspace. This is not an OS sandbox
against another hostile process concurrently changing filesystem links. An
execution sandbox or arbitrary command runner would require another design.

## Deliverable

See the [Milestone 5 implementation plan](../plan/feature-tools-milestone5-v1.md)
for interfaces, file changes, phased tasks, and acceptance tests. The original planning turn left production at Milestone 4; the user subsequently
approved the implementation now documented in Milestone 5.
