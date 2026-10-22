---
goal: Add one reusable Agent and one reliable Coder configuration over the existing structured LLM client
version: 1.0
date_created: 2026-10-22
last_updated: 2026-10-22
owner: NTG project
status: Completed
tags: [feature, agents, structured-output, milestone4]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Implement Milestone 4 as a small agent module above the existing Milestone 3
client. Repository root is `C:/Users/perur/Desktop/NTG`; all file paths below are
relative to that root. The approved plan is implemented and verified.
Final verification: 109 mocked tests passed; Ruff lint and formatting checks passed.
No live API calls were made.

The feasibility assessment is `docs/milestone4-feasibility.md`. The baseline is
69 passing offline tests. The first Coder generates structured Python source and
an explanation for a caller-supplied request. It does not execute that source.

## 1. Requirements & Constraints

- **REQ-001**: Provide `Agent(name, system_prompt, llm, *, response_model=Answer)` and `run(task: str, context: str | None = None) -> AgentResult`. Roles and output contracts are constructor inputs; the generic Agent contains no coding-specific instructions.
- **REQ-002**: Validate nonempty string name, system prompt, and task; context must be a string or `None`. Empty context is permitted. Validate the response model with the existing `response_schema` helper during construction. Invalid inputs raise the existing `LLMError`/`RoutingError` with status 400 before invoking the client.
- **REQ-003**: Build a fresh message list for every run: one system message containing the configured instructions, followed by one user message containing `json.dumps({"task": task, "context": context}, ensure_ascii=False)`. Preserve task/context values after validation. Context is never inserted into a system message.
- **REQ-004**: Invoke `llm.chat.completions.create(model="router", messages=messages, response_model=self.response_model)` exactly once per Agent run. Read `response.choices[0].message.parsed`; do not extract structured data from natural-language content.
- **REQ-005**: Return a Pydantic `AgentResult[OutputT]` with exactly `agent_name: str`, `output: OutputT`, `model: str`, and `diagnostics: dict[str, Any]`. Support `model_dump(mode="json")` and `model_dump_json()` through Pydantic. Construct output from the validated object, with no additional LLM call.
- **REQ-006**: Preserve status, message, retry timing, transient/failover flags, and routing diagnostics on existing `LLMError` failures. Re-raise a new error with a copied diagnostics mapping plus `agent_name`; do not mutate the original exception or convert failures into successful output.
- **REQ-007**: Provide exactly one initial role factory, `create_coder_agent(llm) -> Agent[CodeOutput]`. Configure name `coder`, a Python engineering system prompt, and `CodeOutput` containing `code: StrictStr` and `explanation: StrictStr`. Require both to be nonblank. Validate code with `ast.parse` and return its original text unchanged. Do not execute generated code.
- **REQ-008**: The Coder instructions require implementation of the caller's request, use of provided context, and raw Python source inside the `code` JSON field without Markdown fences. They do not embed a particular programming problem or output filename.
- **REQ-009**: Make `main.py` an example of one Coder run. Accept a required positional task string and optional `--context` text through `argparse`; call the Coder and print `AgentResult.model_dump_json(indent=2)`. Retain the existing `cli_entrypoint` error formatter. Document that running without a task prints usage and exits with argparse status 2.
- **CON-001**: Reuse current routing, provider configuration, secrets, response-schema handling, retries, persistent SQLite state, and compatibility imports. Add no local quota limits or provider credentials to agent code.
- **CON-002**: Add no tool execution, filesystem writing of generated code, shell execution, agent-to-agent orchestration, planning workflow, persistent conversation memory, or model-driven repair loop. There is no `tools` argument pretending to support functionality that the client does not implement.
- **CON-003**: No live API calls. Use fake clients/providers and temporary databases. Do not run the production CLI against configured credentials during verification.
- **SEC-001**: Agent results and errors must never include API keys or raw provider exceptions. Do not automatically log or echo task/context in error diagnostics. Preserve the router's existing enforcement behavior.
- **GUD-001**: Keep validation functions pure and idempotent because the current client may validate a response model more than once. Do not perform I/O or modify generated source from a validator.
- **PAT-001**: Use composition and an injected LLM client. Keep constructor configuration read-only during runs and all per-run messages/results local, so repeated and concurrent calls do not share context or history.

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: Implement and verify the generic agent contract independently of provider access. Completion criterion: agent unit tests pass for message construction, typed results, isolation, and error propagation.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Create `tests/test_agent.py` with a recording fake client implementing the existing `chat.completions.create` interface. Add tests for REQ-001 through REQ-006 and PAT-001. The fake returns the existing typed `ChatCompletion`; no credentials or service construction are required. | Yes | 2026-10-22 |
| TASK-002 | After TASK-001, add `ntg_llm/agent.py` with generic `AgentResult[OutputT]` and `Agent[OutputT]`, using `OutputT` bounded by Pydantic `BaseModel`. Implement constructor validation, stateless `run`, message serialization, one client invocation, typed result construction, and copied error diagnostics exactly as specified above. Do not catch unrelated programming exceptions and relabel them as provider errors. | Yes | 2026-10-22 |
| TASK-003 | After TASK-002, run `python -m pytest tests/test_agent.py -q`. Resolve contract failures before beginning Phase 2. | Yes | 2026-10-22 |

### Implementation Phase 2

- **GOAL-002**: Demonstrate the generic class through one Coder configuration using the existing completion stack. Depends on GOAL-001. Completion criterion: valid generated Python reaches a typed result, while malformed or invalid output fails predictably without extra agent retries.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | Create `tests/test_coder.py`. Exercise the factory through `LLMClient(service=CompletionService(..., router_factory=FakeRouter))`, using a temporary SQLite database. Add the valid-code, invalid-output, syntax, diagnostics, and one-client-invocation cases described in the Testing section. | Yes | 2026-10-22 |
| TASK-005 | After TASK-004, add `ntg_llm/coder.py` with `CodeOutput`, check-only field validators, the role instructions, and `create_coder_agent(llm)`. Use `ConfigDict(extra="forbid")`, `StrictStr`, and `ast.parse`; catch `SyntaxError` and convert it to a validation `ValueError` without returning generated source in error text. | Yes | 2026-10-22 |
| TASK-006 | After TASK-005, add exports for `Agent`, `AgentResult`, `CodeOutput`, and `create_coder_agent` to `ntg_llm/__init__.py`. Preserve the existing exports and root compatibility modules. Verify imports do not initialize a service or make provider calls. | Yes | 2026-10-22 |
| TASK-007 | After TASK-006, run `python -m pytest tests/test_agent.py tests/test_coder.py tests/test_structured_output.py tests/test_client.py -q`. Fix failures before Phase 3. | Yes | 2026-10-22 |

### Implementation Phase 3

- **GOAL-003**: Integrate the single-agent entry point and document the completed behavior. Depends on GOAL-002. Completion criterion: the CLI works with a mocked client, documentation matches the actual interface, and all repository checks pass.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-008 | Update `main.py` according to REQ-009. Instantiate `LLMClient`, construct one Coder through the factory, run the parsed task/context, and print the typed result as JSON. Keep role instructions, response models, secrets, and routing implementation out of this entry point. Remove its unused legacy `structured_completion` import; that function remains publicly available from `ntg_llm` and `llm`. | Yes | 2026-10-22 |
| TASK-009 | After TASK-008, update the main-entry-point tests in `tests/test_client.py` and add `tests/test_main.py` for required task handling, optional context, success JSON, and sanitized CLI failure. Patch `sys.argv` and inject a fake LLM. Keep an independent test of the legacy `structured_completion` function instead of requiring main.py to re-export it. | Yes | 2026-10-22 |
| TASK-010 | After TASK-009, add `docs/milestone4.md` and update `README.md` to describe the Agent contract, one Coder, caller-defined roles/schemas, CLI arguments, typed results, inherited retry semantics, tool-free scope, and syntax-versus-semantic correctness. Retain the Milestone 3 document as historical capability documentation and link the new milestone. | Yes | 2026-10-22 |
| TASK-011 | After TASK-010, run `python -m pytest -q`, `python -m ruff check .`, and `python -m ruff format --check .`. Check that the tests use only fake transports and temporary state. Record actual results in the milestone document and mark this plan Completed only after every requirement is met. | Yes | 2026-10-22 |

## 3. Alternatives

- **ALT-001**: Subclass `LLMClient` for Coder behavior. Rejected because role-specific instructions would become coupled to the completion interface and later roles would require additional client subclasses.
- **ALT-002**: Use an agent framework with tool dispatch and a multi-step loop. Deferred because the current client has no tool-call protocol and the requested first-agent behavior fits one existing completion invocation.
- **ALT-003**: Return only the generated text or parsed payload. Rejected because the caller needs agent identity and existing routing/failure diagnostics alongside typed output.

## 4. Dependencies

- **DEP-001**: Existing `LLMClient.chat.completions.create`, typed `ChatCompletion`, `LLMError`, and the Milestone 3 schema validation path.
- **DEP-002**: Existing Pydantic, pytest, and Ruff dependencies; standard-library `json`, `argparse`, `typing`, and `ast`. No new package dependency is required.
- **DEP-003**: `tests/conftest.py` live-HTTP blocker and `tests/helpers.py` fake provider/deployment builders. Production `.env` and SQLite state are not test inputs.

## 5. Files

- **FILE-001**: Add `ntg_llm/agent.py` for the generic Agent and AgentResult.
- **FILE-002**: Add `ntg_llm/coder.py` for CodeOutput and the initial Coder factory.
- **FILE-003**: Update `ntg_llm/__init__.py` public exports.
- **FILE-004**: Update `main.py` to accept the caller's task/context and invoke one Coder.
- **FILE-005**: Add `tests/test_agent.py`, `tests/test_coder.py`, and `tests/test_main.py`; adjust the existing main tests in `tests/test_client.py`.
- **FILE-006**: Add `docs/milestone4.md` and update `README.md`; retain the feasibility assessment and this implementation plan.

## 6. Testing

- **TEST-001**: Check missing/blank/wrong-type name, system prompt, task, context, and unsupported response models. Invalid input must fail before the fake client's call count changes.
- **TEST-002**: Assert exact configured system instructions, JSON-decoded task/context values, model alias, and response model forwarding. Test `None`, empty context, Unicode, quotes, and embedded text resembling role delimiters without changing message roles.
- **TEST-003**: Exercise two different output schemas through the same Agent class to prove that generic agent behavior is independent of coding. Verify AgentResult's typed output and complete JSON serialization.
- **TEST-004**: Run the same Agent repeatedly and concurrently with distinct task/context values using a thread-safe recording fake. Verify there is no message accumulation or context leakage between calls.
- **TEST-005**: Make the fake client raise an `LLMError` with retry timing and failed attempts. Verify one client call, unchanged original exception diagnostics, a copied error with agent identity, and preservation of status/flags/retry timing. Ensure no raw task/context or credential sentinel is added.
- **TEST-006**: Run the Coder with mocked valid Python source and explanation. Verify source and explanation reach the typed result unchanged, diagnostics identify the selected deployment, and no generated-source file is written or generated code executed. The router may update its temporary SQLite state as usual.
- **TEST-007**: Return malformed JSON, wrong field types, missing fields, unknown fields, blank code/explanation, Markdown-fenced source, and invalid Python syntax. Each invalid output must produce a sanitized 502 and one upstream attempt; the Agent adds no retries or repair call.
- **TEST-008**: Use an existing-style transient-failure fake that succeeds after router retries. Verify one Agent/client invocation and the expected multiple upstream attempts, with all failed attempts retained in AgentResult diagnostics.
- **TEST-009**: Mock the CLI's LLM, provide task/context arguments, and parse stdout as AgentResult JSON. Verify provider failure uses the existing JSON error formatter and exit status 1; missing task uses argparse usage and exit status 2.
- **TEST-010**: Run the complete existing suite plus new tests and Ruff checks. Confirm persistence, concurrency, provider routing, schema validation, legacy imports, and secret redaction continue to pass.

## 7. Risks & Assumptions

- **RISK-001**: Schema-valid and syntactically valid Python can still implement the wrong behavior. This milestone reports generation success, not a claim of tested code correctness. Executing generated code needs a separately designed sandbox and evaluation scope.
- **RISK-002**: The existing client may validate Pydantic models more than once. The planned Coder validators must be check-only and idempotent; a broader validation-lifecycle refactor is outside this plan.
- **RISK-003**: Model availability and schema-feature support vary by provider. Existing failures remain explicit and diagnosable; passing mocked tests does not establish live availability.
- **RISK-004**: Prompt context may contain conflicting instructions. It stays in a user message and is never promoted into the configured system prompt; role separation alone does not guarantee model compliance.
- **ASSUMPTION-001**: The user approved the implementation after reviewing the feasibility assessment and plan. The application now includes the Milestone 4 Agent and Coder interfaces.
- **ASSUMPTION-002**: Start with one Python Coder configuration. The generic Agent supports other roles and schemas through configuration, but no additional named agents are introduced.
- **ASSUMPTION-003**: The first `context` interface accepts text or None. Callers with structured context serialize it to text; file loading and repository discovery are not implicit Agent behavior.

## 8. Related Specifications / Further Reading

- [Milestone 4 feasibility assessment](../docs/milestone4-feasibility.md)
- [Milestone 3 contract](../docs/milestone3.md)
- [Existing client implementation](../ntg_llm/client.py)
- [Existing completion and retry implementation](../ntg_llm/service.py)
- [Existing typed response envelopes](../ntg_llm/responses.py)
- [Existing schema validation](../ntg_llm/schemas.py)
