# Milestone 6 — LangChain migration feasibility

Date: 2026-10-22. Status: implemented and verified.

## Assessment before implementation

The repository's prior runtime was an NTG-owned loop in `tool_runtime.py`.
`ToolRegistry.build_decision_model` generated custom `action=tool/final` contracts;
`run_with_tools` executed actions, inserted observations, and selected the next
model decision. It was a ReAct-inspired implementation, not a LangChain agent.
The previous verification-only/explicit-prompt milestone plans are superseded by
the user's request for LangChain ownership.

The existing router stack is ready to serve as the backend: `LLMClient` delegates
to `CompletionService`, which owns round-robin selection, provider retries,
fallback, and sanitized errors. The interface supports structured text responses,
not native provider function calls. Filesystem handlers and strict registry
validation can be reused unchanged beneath LangChain tools.

## Verified API selection

Installed versions were LangChain 1.3.1, core 1.4.0, classic 1.0.7, and LangGraph
1.2.0. PyPI reported latest stable versions 1.4.7, 1.6.13, 1.0.8, and 1.2.16,
respectively. Official docs recommend `langchain.agents.create_agent` for modern
native tool-calling models. The exact requested `create_react_agent` and
`AgentExecutor` remain available under `langchain_classic.agents`.

Select and pin `langchain-classic==1.0.8` plus `langchain-core==1.6.13` for this
text-only router. The official classic factory accepts a custom prompt and an
official `JSONAgentOutputParser`, supporting dictionary action inputs
for typed multi-field writes. Set `stop_sequence=False` because the current NTG
client does not expose provider stop sequences. Use a nonstreaming runnable agent.

This is the older classic implementation; its official source recommends modern
`create_agent` for new production applications. It is selected for compatibility,
not presented as modern LangChain's preferred API. A modern native-call migration
would additionally need `bind_tools`, native tool-message support, provider
capability handling, and structured-output strategy changes beyond this task's
preserved text contract.

Checked sources: [official migration guide](https://docs.langchain.com/oss/python/migrate/langchain-v1),
[PyPI classic metadata](https://pypi.org/pypi/langchain-classic/json),
[PyPI core metadata](https://pypi.org/pypi/langchain-core/json), and installed official
factory, executor, tool, and JSON parser source for the pinned releases.

## Responsibility cross-check

| Responsibility | Before | After |
|---|---|---|
| Agent iteration and termination | NTG `run_with_tools` | LangChain `AgentExecutor.invoke` |
| Action interpretation | NTG `AgentDecision` | Official LangChain JSON ReAct parser |
| Action/observation history | NTG manually appended messages | Official LangChain scratchpad rendering |
| Tool dispatch | NTG loop selected registry execution | LangChain invokes a `StructuredTool` |
| Tool permissions and input/output validation | NTG registry and filesystem | Same host validation and filesystem implementation |
| Provider selection/recovery | NTG client/service/LiteLLM | Same implementation, behind `NTGChatModel` |
| Final output contract | Embedded in every intermediate decision | Independently validate LangChain final output |
| Execution ceiling | Custom action bound plus extra final turn | One `AgentExecutor.max_iterations` bound |
| Routing/action diagnostics | Custom runtime aggregation | Per-run adapter/tool evidence, no raw trajectories |
| Direct completion and no-tools Agent | Direct NTG calls | Same direct calls; lazy framework import |

## Concrete migration

1. Pin official packages; add a `BaseChatModel` adapter around the existing client.
2. Convert immutable tool registrations to typed `StructuredTool` instances.
   Use closed JSON argument schemas so strict host validation sees original types
   and required fields instead of LangChain-coerced values or inserted defaults.
3. Replace the runtime with one factory/executor invocation and final validation.
   Remove `_Decision`, `build_decision_model`, manual history, and the custom loop.
4. Map `max_tool_calls` directly to the executor iteration bound; document that
   final output consumes an iteration and exhaustion returns sanitized 508.
5. Retain safe run-local diagnostics, prevent trace uploads/verbose output, and
   preserve provider errors and partial-write evidence.
6. Verify actual executor/tool/model paths, observation-dependent behavior,
   recovery, limits, strict output, no replay, direct APIs, and a real temporary
   workspace greeting edit. Run the complete offline suite and Ruff.

No custom Thought/Action parser, router rewrite, new provider SDK, shell tools,
planner/reviewer roles, or parallel agent loop is introduced. The filesystem
policy is a trusted-workspace boundary rather than an OS execution sandbox.

## Verification boundary

The previous custom-loop baseline was 203 passed and one Windows symlink skip.
New tests use the actual LangChain runtime with mocked provider responses and
temporary SQLite/workspace state. They can prove host integration and side effects,
but do not establish live model quality. Live HTTP is blocked by the shared test
fixture. Configuration, credentials, and production SQLite state remain untouched.

Migration results: **209 passed, 1 skipped** in the complete suite. Ruff lint and
formatting and CLI help passed. The separate instrumented greeting E2E passed,
confirming actual LangChain execution and an on-disk edit through NTG handlers.
Nine client/routing/recovery/schema/filesystem implementation files were compared
byte-for-byte against the published baseline and remain unchanged. Platform
skip and optional installed-package warnings are recorded in the usage document.

See [current usage](milestone6.md) and the
[migration plan](../plan/refactor-langchain-react-v1.md).
