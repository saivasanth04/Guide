---
goal: Transfer agent orchestration to LangChain while preserving NTG routing and tools
version: 1.0
date_created: 2026-10-22
last_updated: 2026-10-22
owner: NTG
status: Completed
tags: [refactor, langchain, react]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Replace NTG's custom decision loop with the real classic LangChain ReAct factory
and executor. The user supplied the architecture and authorized implementation
after a short assessment. The assessment and exact plan were presented in chat
before production edits; the dependency pins are the first implementation change.

## 1. Requirements & Constraints

- **REQ-001**: `create_react_agent` and `AgentExecutor.invoke` own all agent iteration, parsing, scratchpad updates, dispatch, and stopping.
- **REQ-002**: `NTGChatModel` forwards model requests exclusively to `LLMClient`; NTG retains provider selection/recovery.
- **REQ-003**: LangChain typed tools delegate to the existing registry and unchanged filesystem policy.
- **REQ-004**: Validate only final answer text as the caller's response model; preserve `AgentResult` and sanitized per-turn/action diagnostics.
- **CON-001**: One authoritative ceiling: `AgentExecutor.max_iterations=max_tool_calls`, with a single-action parser. The final model decision occupies an iteration; remove the previous extra-final-turn promise.
- **CON-002**: No new router, provider integrations, model selection, action replay, hidden retries, tracing uploads, verbose reasoning output, shell tools, or parallel custom loop.
- **SEC-001**: Disable LangSmith tracing for agent execution and model calls; never include action logs or contents in diagnostics/errors.
- **PAT-001**: Use official `JSONAgentOutputParser` for JSON object arguments. NTG does not parse Action/Thought text itself.

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: Establish real LangChain integration and remove the obsolete architecture.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Verify installed/latest package metadata and official source/docs. Pin `langchain-classic==1.0.8` and `langchain-core==1.6.13` in `pyproject.toml`; install and verify imports. | Yes | 2026-10-22 |
| TASK-002 | Add `ntg_llm/agent_trace.py` for safe summaries/diagnostic aggregation and `ntg_llm/langchain_model.py` for `NTGChatModel._generate`, text-message conversion, uncached nonstreaming model calls, and per-invocation routing evidence. | Yes | 2026-10-22 |
| TASK-003 | Add `ntg_llm/langchain_tools.py` to convert registered `ToolSpec` entries to `StructuredTool` objects. Delegate execution and strict validation to the registry; convert expected failures to fixed observations and stop unexpected failures with partial-effect evidence. | Yes | 2026-10-22 |
| TASK-004 | Replace `ntg_llm/tool_runtime.py` with one-shot construction/invocation of the official factory/executor and final-output validation. Remove `run_with_tools`, custom history construction, and all manual decision iteration. Remove `_Decision`/`build_decision_model` from the registry; expose immutable registered specs. Update Agent dispatch and CLI limit description. | Yes | 2026-10-22 |

### Implementation Phase 2

- **GOAL-002**: Verify behavior and document the authoritative architecture. Depends on GOAL-001.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Migrate agent/CLI/registry tests to real LangChain Action/Final Answer text transported by `Answer.answer`. Add adapter and typed-tool tests; instrument real executor/tool calls, adaptive scratchpad behavior, bounds, malformed output, failure semantics, and sequential/concurrent isolation. | Yes | 2026-10-22 |
| TASK-006 | Add an instrumented temporary-workspace greeting edit test through Coder -> real executor -> NTGChatModel -> client/service/router -> real tools. Prove a retried provider completion does not replay a write. Preserve the existing routing/direct-API/security suites. | Yes | 2026-10-22 |
| TASK-007 | Rewrite current Milestone 6 docs and README; mark old Milestone 5/6 custom-loop descriptions as historical. Explain API choice, pinned dependencies, final schema validation, diagnostics/privacy, exact new bound, and live verification limits. | Yes | 2026-10-22 |
| TASK-008 | Run `python -m pytest -q`, Ruff lint/format, CLI help, and the temporary-workspace E2E. Review scoped changes against the published baseline; record results and complete this plan. | Yes | 2026-10-22 |

## 3. Alternatives

- **ALT-001**: Modern `langchain.agents.create_agent` is the official recommendation for native tool-calling chat models, but NTG exposes validated text only. Adding native tool-call transport and structured-output strategies expands this migration beyond the existing router contract.
- **ALT-002**: Retaining the custom loop beneath LangChain would leave NTG authoritative and fails the requested architecture.
- **ALT-003**: Classic default single-string action parsing cannot handle typed multi-field writes cleanly. Use its official JSON action parser with dictionary `action_input` instead.

## 4. Dependencies

- **DEP-001**: `langchain-classic==1.0.8` and `langchain-core==1.6.13`; retain existing LiteLLM and validation dependencies. No `langchain-openai` dependency.
- **DEP-002**: Existing filesystem policy, client/service/router, mock provider transport, and temporary SQLite test state.

## 5. Files

- **FILE-001**: `ntg_llm/agent_trace.py`, `ntg_llm/langchain_model.py`, `ntg_llm/langchain_tools.py` — new NTG adapters and safe diagnostics.
- **FILE-002**: `ntg_llm/tool_runtime.py`, `ntg_llm/tools/registry.py`, `ntg_llm/agent.py`, `main.py`, `pyproject.toml` — integration, obsolete code removal, and limits/dependencies.
- **FILE-003**: `tests/test_tool_agent.py`, `tests/test_tool_registry.py`, `tests/test_main.py`, `tests/test_langchain_model.py` — real framework path and regressions.
- **FILE-004**: `README.md`, `docs/milestone6.md`, `docs/milestone6-feasibility.md`, historical milestone/plan notices, and this plan — accurate architecture and verification.

## 6. Testing

- **TEST-001**: Actual `AgentExecutor.invoke` and `StructuredTool` instrumentation; basic/list-read-write/final flow and observation-dependent recovery in both provider response modes.
- **TEST-002**: Maximum iterations, no over-limit action, honest controlled failure, malformed/final output validation, and expected versus unexpected tool failures.
- **TEST-003**: Provider transient retry/fallback/429 preserve metadata and never replay completed writes.
- **TEST-004**: Direct APIs and tool-free agents work independently without importing the executor; no verbose/private reasoning or tracing uploads escape.
- **TEST-005**: Full offline suite and existing filesystem/rotation/provider security coverage pass, with any platform-specific skip documented.

## 7. Risks & Assumptions

- **RISK-001**: The official classic factory is an older API; pinning/source verification and explicit docs prevent confusing it with modern native-tool agents.
- **RISK-002**: A new iteration-limit mapping removes the previous extra final decision. Document and test it explicitly rather than maintaining another action counter.
- **RISK-003**: LangChain parsers/verbose callbacks can include raw model text in errors. Sanitize exceptions, disable tracing/verbose output, and retain only safe host evidence.
- **ASSUMPTION-001**: Existing provider structured text output can transport LangChain ReAct text inside `Answer.answer`; final output is independently validated afterward.
- **ASSUMPTION-002**: Offline integration establishes architecture/control flow and filesystem effects; live provider reasoning quality is outside that evidence.

## 8. Related Specifications / Further Reading

- [LangChain migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1)
- [Official classic ReAct factory](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/agents/react/agent.py)
- [Official JSON ReAct parser](https://github.com/langchain-ai/langchain/blob/master/libs/langchain/langchain_classic/agents/output_parsers/json.py)
- [Milestone 6 usage](../docs/milestone6.md)
