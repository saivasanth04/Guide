---
goal: Give the existing Agent typed workspace tools and a bounded execution-feedback loop
version: 1.0
date_created: 2026-10-22
last_updated: 2026-10-22
owner: NTG project
status: Completed
tags: [feature, tools, agents, filesystem, milestone5]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Extend Milestone 4 with registered host-executed tools. The model requests actions
using structured JSON, the application validates and performs them, and the model
receives their results before returning its final typed output. Repository root:
`C:/Users/perur/Desktop/NTG`. Paths below are relative to that directory.

The [feasibility assessment](../docs/milestone5-feasibility.md) records a passing
109-test baseline and an offline write/list/read/final integration probe. This
plan has been implemented after user approval. Final verification: 195 tests
passed and one native-symlink test was skipped because this Windows host cannot
create the link; separate reparse and hard-link tests passed. Ruff lint and
formatting checks passed. No live provider calls were made.

## 1. Requirements & Constraints

- **REQ-001**: Add a frozen `ToolSpec` with `name`, `description`, `input_model`, `output_model`, and a bound Python `handler`. Use explicit registration only. Names must match `[a-z][a-z0-9_]*`; reject duplicates or unsupported schemas at registry construction with a sanitized 400. Never resolve a requested name using `eval`, imports, `getattr`, or shell evaluation.
- **REQ-002**: Add an immutable `ToolRegistry(specs)` with `catalog()`, `build_decision_model(final_model)`, and `execute(name, arguments, *, call_id)`. The catalog contains only registered names, descriptions, and input schemas. Validate arguments strictly before calling a handler, and validate its typed result. Tools execute only in this method, never inside Pydantic validators.
- **REQ-003**: Generate closed input-specific request models with `name: Literal[registered_name]` and `arguments: RegisteredInput`. Build a closed decision model with required `action: Literal['tool', 'final']`, `tool: Union[RegisteredCalls] | None`, and `output: FinalModel | None`. A pure model validator requires nonnull tool/null output for tool actions and null tool/nonnull output for final actions. Validate the generated schema through the existing `response_schema` helper before the first model call. Do not use an open dictionary for arguments.
- **REQ-004**: Add typed `ToolError` and generic `ToolResult`: call ID, tool name, success flag, typed data or null, and structured failure or null. Known errors use fixed codes/messages such as `not_found`, `path_denied`, `already_exists`, `invalid_arguments`, `too_large`, `unsupported_encoding`, and `io_error`. Raw OS/provider exceptions, absolute paths, and file contents must not appear in error messages. An unknown name never invokes a handler. Expected tool errors are returned as observations; unexpected handler or output-validation errors stop the run with sanitized status 500.
- **REQ-005**: Add `FilesystemTools(root, *, allow_overwrite=False, max_read_bytes=262144, max_write_bytes=262144, max_list_entries=1000)` and `create_filesystem_registry(...)`. Resolve and validate an existing directory once and bind it in the handlers. Validate all configuration values before I/O/model calls: byte/entry bounds are positive integers excluding booleans; allow_overwrite is a boolean. No model argument can change root or host policy.
- **REQ-006**: Register three initial operations: `read_file(path)` returning relative path/content; `list_files(path)` returning relative path, sorted immediate entries with name/type, and an explicit truncated flag; `write_file(path, content, overwrite=False)` returning relative path, bytes_written, and created flag. All arguments are named Pydantic fields with strict types. Read and write UTF-8 only, preserving text. Read at most the configured byte bound plus one to detect oversize; reject oversized reads/writes rather than returning partial file content. Listings are nonrecursive, sorted by `(name.casefold(), name)`, and return at most max_list_entries with truncated=true when needed.
- **REQ-007**: Writes may create missing parent directories after path-policy checks. Deny replacement unless both bound host policy and the call's overwrite field allow it. Stage bytes in a temporary file inside the target directory, flush/fsync, recheck policy, and publish atomically. Use exclusive no-replace publication for creates (`os.link` of the staged file, then unlink the temporary name); use `os.replace` only for authorized replacements. Never downgrade an unsupported exclusive publication to an overwriting operation. Remove staging files on failure. Expected concurrent create conflicts return already_exists; permitted replacements have documented last-writer-wins semantics.
- **REQ-008**: Extend `Agent` with optional keyword-only `tools: ToolRegistry | None = None` and `max_tool_calls: int = 8`. Validate the budget as a positive integer excluding booleans. No/empty tools preserve the existing Milestone 4 one-call behavior and result shape. A tools-enabled run delegates to a new `run_with_tools` module, using only system/user/assistant text messages supported by the current client.
- **REQ-009**: Each tool decision turn invokes the client once with the generated decision model. Add the host catalog and fixed decision/observation instructions to system text while preserving the configured role. Put task/context and later observations in user messages. After a valid tool decision, assign a host call ID `tool_001`, then execute it once. Append the assistant decision JSON and a user observation JSON `{type: 'tool_result', result: ...}` to local history. Do not promote file contents into system instructions. Final decisions return their typed output without executing an implicit action.
- **REQ-010**: A run performs at most max_tool_calls action attempts and at most max_tool_calls+1 client invocations. Count successful and failed executions against the action budget. If the final allowed turn still requests a tool, execute nothing and raise sanitized status 508 with code `tool_limit_reached` in diagnostics. Malformed/invalid decisions stop with the existing sanitized 502; add no repair call. Provider errors retain their existing status/retry timing and terminate the current run. A tool's expected failure may be followed by a different valid model decision within the same budget.
- **REQ-011**: Preserve `AgentResult`'s four fields. For tools-enabled runs, retain final model/deployment metadata and aggregate routing attempts, failed_attempts, and attempt_count across model turns; add turn_number to flattened attempt records. Include per-turn diagnostics under agent_steps and content-free execution summaries under tool_calls. Summaries contain call ID, tool name, permitted relative path, success/error code, and byte/entry counts when applicable; never include read/write content or full argument blobs. Error exits carry the same accumulated trace, including completed writes. Copy diagnostics rather than mutating provider errors.
- **REQ-012**: Extend `create_coder_agent` to accept tools and max_tool_calls. With tools, use role instructions permitting workspace inspection and explicit file writes through registered requests; remove the contradictory source-only/no-writing instruction in this mode. Retain `CodeOutput` as final output. Without tools, preserve existing instructions and behavior. Do not infer a write from the final code field or claim success for an action absent from the execution trace.
- **REQ-013**: Extend `main.py` with optional `--workspace PATH`, `--allow-overwrite`, and `--max-tool-calls N` (default 8). Existing task and --context remain. Without --workspace use the existing Coder. With --workspace bind the filesystem registry and use the tool-enabled Coder. The workspace must already exist; invalid paths/budgets return sanitized 400 before any model call. --allow-overwrite without --workspace is argparse misuse with status 2. Help/usage initialize no client and perform no tool I/O. Print one final AgentResult JSON or the existing formatted CLI error.
- **SEC-001**: Resolve every target beneath the bound canonical root before I/O. Accept only nonempty relative paths; list_files alone permits '.'. Reject parent traversal, absolute/drive-relative/UNC/device paths, NUL/control characters, Windows alternate-stream colons, wildcards, Windows reserved device components, and trailing spaces/dots. Normalize separators consistently. Reject symlinks, reparse points/junctions within the workspace path, nonregular read/write targets, and existing file targets with st_nlink greater than one. Recheck before publishing writes. Do not recursively follow filesystem links.
- **SEC-002**: Deny any path component matching `.env*` case-insensitively, `beatapi.txt`, `.git`, `.venv`, `__pycache__`, `.ssh`, `.aws`, `.config`, `.codex`, or `.agents`; deny SQLite filenames matching `*.sqlite3*` or `*.db`, and internal staging names prefixed `._ntg_tmp_`. Apply the same policy to reads and writes, and omit protected names and link entries from listings. Use a dedicated workspace containing only model-visible data; filename rules do not identify arbitrary secrets.
- **SEC-003**: Execute each accepted action once, after validated completion, with no tool retry decorator or automatic replay. Router retries occur before a decision is returned and must not repeat earlier file actions. Never execute generated code, commands, deletion, network calls, or arbitrary Python selected by the model. Tool effects are not rolled back on later model failure.
- **CON-001**: Preserve provider routing, persistent rotation, configured retries/fallback, enforcement stops, credentials, and lack of assumed provider quota limits. Tool action/payload bounds are separate host execution policy, not RPM/TPM/RPD accounting.
- **CON-002**: No live API calls or production filesystem actions during verification. Use fake providers, temporary roots, and temporary SQLite state. Do not read the real .env or reset the live database. Implementation was approved after the planning turn; all verification used temporary files and mocked providers.
- **CON-003**: Add no agent framework, native provider function-calling API, shell tool, tool discovery from model-provided imports, multi-agent scheduler, persistent memory, or crash-resume queue. Standard-library filesystem/Pydantic facilities suffice; no new package dependency is required.
- **PAT-001**: Keep tool definitions/policy separate from orchestration. Registries are immutable; histories, budgets, call IDs, and traces are local to each run. The tool runtime must not import Agent, avoiding a circular dependency; it returns an internal typed outcome which Agent wraps in AgentResult.

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: Establish typed registration and validated decisions. Completion criterion: registry/model tests pass without filesystem or provider access.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add `tests/test_tool_registry.py` covering duplicate/invalid registration, strict argument contracts, unknown names, nested decision schemas, exclusive final/tool branches, typed results, sanitized expected errors, and output-validation failure. Verify handlers are not invoked by schema generation or validation. | Yes | 2026-10-22 |
| TASK-002 | After TASK-001, add `ntg_llm/tools/models.py`, `ntg_llm/tools/registry.py`, and `ntg_llm/tools/__init__.py`. Implement ToolSpec, ToolError, ToolResult, immutable ToolRegistry, input-specific dynamic request/decision models using Pydantic create_model, and typed dispatch per REQ-001 through REQ-004. Export the tool interface from the tools package only at this phase. | Yes | 2026-10-22 |
| TASK-003 | After TASK-002, run `python -m pytest tests/test_tool_registry.py -q`; verify closed schema generation through response_schema and that no test performs any action merely by parsing a model response. | Yes | 2026-10-22 |

### Implementation Phase 2

- **GOAL-002**: Implement explicit workspace filesystem capabilities. Depends on GOAL-001. Completion criterion: real temporary-file operations and path/permission tests pass, with atomic publication and no workspace escapes.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Add `tests/test_filesystem_tools.py` for read/write/list behavior, strict inputs, Unicode, size bounds, protected names, missing targets, empty root listing, traversal/Windows paths, link/reparse/hard-link handling, overwrite policy, temporary-file cleanup, and concurrent creates/replacements. All paths are under pytest tmp_path or a deliberately separate temporary outside directory used to assert denied access. | Yes | 2026-10-22 |
| TASK-005 | After TASK-004, add `ntg_llm/tools/filesystem.py` with typed argument/result models, root-bound FilesystemTools, path/payload policy, staging/publication helpers, and create_filesystem_registry. Implement REQ-005 through REQ-007 and SEC-001/SEC-002. Use lstat and Windows reparse attributes where available; do not rely solely on lexical prefix checks. Return only relative paths. | Yes | 2026-10-22 |
| TASK-006 | After TASK-005, run `python -m pytest tests/test_tool_registry.py tests/test_filesystem_tools.py -q`. Native symlink/junction tests may skip only when the host cannot create them; keep lexical path and simulated reparse-policy tests mandatory. Verify tests leave no staging artifacts or changed outside files. | Yes | 2026-10-22 |

### Implementation Phase 3

- **GOAL-003**: Execute a bounded tool-feedback loop without changing transport or no-tools semantics. Depends on GOAL-002. Completion criterion: a scripted model successfully reads/writes/lists files and finishes; failure/budget traces preserve all preceding effects.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | Add `tests/test_tool_agent.py` using the actual LLMClient and a scripted FakeRouter with a temporary filesystem registry. Cover tool/final branch handling, feedback history, model turns, action budgets, expected tool errors, provider retry/failure after a write, malformed output, generic final schemas, and independent concurrent runs. | Yes | 2026-10-22 |
| TASK-008 | After TASK-007, add `ntg_llm/tool_runtime.py` with `run_with_tools` and an internal ToolRunOutcome. Implement REQ-009 through REQ-011: generate the decision schema before actions, maintain local text history, execute one validated call at a time, sanitize observations, aggregate diagnostics, and stop predictably. Do not retry a handler or import Agent. | Yes | 2026-10-22 |
| TASK-009 | After TASK-008, extend `ntg_llm/agent.py` with optional tools/max_tool_calls and delegation to the runtime while preserving its no-tools branch. Validate budgets/configuration before any completion or action. Extend `ntg_llm/coder.py` as REQ-012 specifies, and export ToolRegistry/create_filesystem_registry from `ntg_llm/__init__.py` without initializing a workspace or service on import. | Yes | 2026-10-22 |
| TASK-010 | After TASK-009, run `python -m pytest tests/test_tool_agent.py tests/test_agent.py tests/test_coder.py tests/test_structured_output.py -q`. Verify no-tools requests still make one client call, model errors keep their status/timing, and execution observations do not alter provider message validation. | Yes | 2026-10-22 |

### Implementation Phase 4

- **GOAL-004**: Expose explicit filesystem capability through the CLI and document actual behavior. Depends on GOAL-003. Completion criterion: temporary-workspace CLI tests and all repository checks pass.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Extend `main.py` with REQ-013 flags. Build a registry only for an explicit workspace. Keep filesystem implementation, decision schemas, and orchestration out of the CLI. Add `agent_workspace/` to `.gitignore` for the documented sample workspace; do not create that directory during implementation. | Yes | 2026-10-22 |
| TASK-012 | After TASK-011, extend `tests/test_main.py` for valid tool runs in tmp_path, no-tools compatibility, overwrite gating, invalid workspaces/budgets, help/usage without initialization, and failure JSON that retains completed writes. All completion responses remain scripted. | Yes | 2026-10-22 |
| TASK-013 | After TASK-012, add `docs/milestone5.md` and update README.md with the tool lifecycle, required workspace, create-versus-overwrite policy, protected paths, payload/action bounds, safe traces, partial effects, and tool-free compatibility. Retain Milestone 4 as the prior capability document. Include caller-selected task examples without hardcoded application behavior. | Yes | 2026-10-22 |
| TASK-014 | After TASK-013, run `python -m pytest -q`, `python -m ruff check .`, and `python -m ruff format --check .`. Record actual counts/results in the milestone document. Mark this plan Completed only after every requirement and check passes; update feasibility status with a link to implemented behavior. | Yes | 2026-10-22 |

## 3. Alternatives

- **ALT-001**: Native provider function calling. Deferred because the current facade and message normalizer do not accept tool messages/call structures; it requires broader adapter compatibility work than this milestone needs.
- **ALT-002**: A registry that returns action JSON without executing it and feeding back results. Rejected because it does not deliver the milestone's host-executed action behavior.
- **ALT-003**: Open-ended argument dictionaries or arbitrary Python evaluation. Rejected because they bypass current schema constraints and host control over capabilities.
- **ALT-004**: Copy the agent loop into a separate Coder subclass. Rejected because tool use belongs in the generic Agent interface and must remain independent of coding roles and filenames.

## 4. Dependencies

- **DEP-001**: Existing Agent, AgentResult, LLMClient, ChatCompletion, response_schema/validate_response, and RoutingError. No change to native provider message protocols is required.
- **DEP-002**: Existing Pydantic/jsonschema/pytest/Ruff plus standard-library pathlib, os, stat, tempfile, json, typing, and immutable mapping support. No new package dependency.
- **DEP-003**: Local filesystem supporting atomic same-directory rename and exclusive publication by hard link. Unsupported create publication must fail explicitly and leave no destination/staging corruption. An adversarial network filesystem is outside the initial operating assumptions.
- **DEP-004**: Existing fake router helpers and the shared live-HTTP blocker; tests must use their own temporary filesystem and SQLite state.

## 5. Files

- **FILE-001**: Add `ntg_llm/tools/__init__.py`, `models.py`, and `registry.py` for typed registration and dispatch.
- **FILE-002**: Add `ntg_llm/tools/filesystem.py` for the initial root-bound tool set and policy.
- **FILE-003**: Add `ntg_llm/tool_runtime.py` for the bounded decision/action/observation loop.
- **FILE-004**: Update `ntg_llm/agent.py`, `coder.py`, and `__init__.py` for optional tool configuration and public exports.
- **FILE-005**: Update `main.py`, `.gitignore`, and README.md; add `docs/milestone5.md`.
- **FILE-006**: Add `tests/test_tool_registry.py`, `tests/test_filesystem_tools.py`, and `tests/test_tool_agent.py`; extend `tests/test_main.py`. Existing tests remain regression coverage.

## 6. Testing

- **TEST-001**: Registry refuses duplicate/invalid names, open argument schemas, unregistered calls, wrong argument types, unknown fields, and invalid handler output; validation alone never calls a handler. Decision models accept exactly one tool or final output and reject unknown tool variants.
- **TEST-002**: Temporary-workspace read/create/list succeeds for normal UTF-8 data, Unicode names, nested parent creation, and '.'. Listings are deterministic/nonrecursive and hide protected entries. Oversized data returns a typed error; oversized listings declare truncation.
- **TEST-003**: Paths outside the root, '..' segments, absolute/drive-relative/UNC/device paths, ADS syntax, reserved names, trailing dots/spaces, symlinks, junctions/reparse points, hard links, directories used as files, and special-file targets cannot be read/written through tools. Assert outside bytes remain unchanged.
- **TEST-004**: Protected environment/credential/state paths are denied without revealing contents or absolute path metadata. Tool traces and error JSON omit file contents, write arguments, raw exceptions, private host paths, and credential sentinels.
- **TEST-005**: Overwrite requires host permission and an explicit true request. Two concurrent exclusive creates yield one success and one already_exists; no mixed/partial bytes appear. Simulate publication/write failure and assert existing content is unchanged and staging files are removed. Permitted replacement is atomic and documented as last-writer-wins.
- **TEST-006**: Script write/list/read/final decisions through the actual client with FakeRouter. Observe real temporary filesystem effects, validate each result's feedback in later messages, and obtain the existing typed final output. No file name or programming task is built into the Agent.
- **TEST-007**: Missing files/denied paths become error observations; the model can select another registered action. Malformed decisions stop with 502 before their handler runs. Budget exhaustion returns 508 after at most max_tool_calls executions and max_tool_calls+1 client calls; the final over-budget request is not executed.
- **TEST-008**: A transient provider failure retries within the same decision turn without repeating any previous file action. A later provider denial, 429, or 503 preserves original status/retry timing plus all completed tool summaries. Successful writes remain on disk and are explicitly reported as partial effects.
- **TEST-009**: Repeated and concurrent Agent runs have independent history, counters, IDs, observations, and traces. Current no-tools Agent/Coder behavior and output shape remain compatible; generic non-Coder final schemas work with the same tool runtime.
- **TEST-010**: CLI tests cover workspace/no-workspace modes, required task, optional context, overwrite gating, invalid budgets/roots, and help without client/workspace initialization. All CLI model responses are mocked and all side effects target tmp_path.
- **TEST-011**: Run all existing 109 baseline tests plus new tests and Ruff checks. Verify routing state, structured validation, enforcement behavior, old imports, and no-live-call protections remain intact.

## 7. Risks & Assumptions

- **RISK-001**: Tool actions are real and can precede a later model failure. A run is not a multi-file transaction and has no crash-resume/exactly-once guarantee. Trace partial effects; do not silently roll them back or replay them.
- **RISK-002**: Canonical path checks and reparse rejection cannot provide an OS sandbox against another hostile local process racing directory changes. Use an exclusively managed trusted local workspace; stronger isolation would need a separate design.
- **RISK-003**: File contents and tool observations can contain instructions or arbitrary sensitive data. Treat them as user data, not system instructions; use a dedicated model-visible root. Protected-name rules cannot classify every secret.
- **RISK-004**: Providers differ in JSON Schema support and live availability. The local typed-union probe passes, but no live provider compatibility claim is made. Unsupported schema/provider errors remain explicit rather than silently switching to unvalidated text.
- **RISK-005**: Existing Pydantic validators may run repeatedly through the client. Decision validators must be pure; tools execute only after the complete parsed decision is returned. Never embed side effects in validation.
- **ASSUMPTION-001**: The user reviewed the feasibility assessment and plan, then approved implementation. The application now supports the optional Milestone 5 filesystem tools and feedback loop.
- **ASSUMPTION-002**: Start with one existing Coder and three local file capabilities. Tool names and task examples illustrate the capability; there is no calculator-specific workflow, task planner, code execution, or implicit write of final output.
- **ASSUMPTION-003**: Byte/list/action bounds are explicit configurable execution policy, distinct from the removed guessed provider limits. Defaults are 256 KiB reads/writes, 1,000 listing entries, and eight tool attempts.

## 8. Related Specifications / Further Reading

- [Milestone 5 feasibility](../docs/milestone5-feasibility.md)
- [Milestone 4 behavior](../docs/milestone4.md)
- [Milestone 3 structured output](../docs/milestone3.md)
- [Current Agent](../ntg_llm/agent.py)
- [Current client](../ntg_llm/client.py)
- [Current schema validation](../ntg_llm/schemas.py)
- [Current completion and retry policy](../ntg_llm/service.py)
