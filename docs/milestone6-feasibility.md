# Milestone 6 — LangGraph migration feasibility

Date: 2026-10-22. The user requested migration from the installed classic LangChain
ReAct executor to LangGraph.

## Assessment and choice

The existing `NTGChatModel`, typed tool adapters, run-local diagnostics and strict
final schemas already isolate framework orchestration from provider routing and
filesystem policy. The migration can replace `tool_runtime.py` and update its
caller, dependency pin, tests and docs without modifying the backend or handlers.

Selected: `StateGraph` with model and tool nodes, conditional completion, and a
back-edge carrying actual observations. LangGraph owns scheduling and state.
Application nodes define the JSON protocol and rendering. Native-tool agent
factories would require new router/tool-message capabilities; retaining the
classic executor would not satisfy the requested migration.

Pin `langgraph==1.2.16` and retain `langchain-core==1.6.13`. Remove the classic
requirement and all runtime imports of its factory, parser and executor.
The [official graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
and [agent examples](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
confirm support for explicit state, cyclic edges and invocation recursion limits.

## Implementation sequence

1. Replace executor construction with a compiled two-node graph and invoke once.
2. Preserve NTG's text-only transport using a strict single JSON action protocol.
3. Use graph recursion_limit=2*N to preserve the existing N-decision limit.
4. Retain strict tool/final validation, sanitized diagnostics and tracing policy.
5. Adapt integration spies to real graph execution; add strict parser and exact
   boundary cases; run the complete suite, Ruff and CLI help.
6. Publish reviewed files to Guide using the isolated publishing checkout.

## Tradeoffs and verification boundary

The old classic parser is replaced by a small host JSON decoder; lists, partial
JSON and duplicate keys are rejected. This intentionally tightens behavior.
Core model/tool types remain in use because LangGraph depends on core. No durable
checkpointing, resume, streaming or parallel tool execution is introduced.
The recursion mapping relies on the two-node topology and is regression-tested.
Real providers are not exercised by the offline tests. Existing filesystem and
provider recovery contracts remain the source of policy.

See [current implementation and usage](milestone6.md).
