# Milestone 4 feasibility assessment

Status: assessment completed before implementation. The approved design is now
implemented; see [Milestone 4](milestone4.md) for current behavior and verification.
The assessment below records the original Milestone 3 baseline.

## Goal and interpretation

Add one reusable Agent class that combines a role, instructions, caller-supplied
context, an LLM client, and an expected output model. Demonstrate it with one
Coder configuration. The example names and programming tasks in the milestone
are illustrative; the generic Agent must accept different roles and schemas.

This is an architectural addition above the existing LLM interface. The current
repository is suitable for it without a provider migration or agent framework.

## Cross-check against the current repository

| Milestone need | Existing evidence | Assessment |
|---|---|---|
| Inject an LLM | `ntg_llm/client.py`, `LLMClient(service=...)` | Ready; existing fake services support offline verification. |
| Give the agent instructions | `CompletionService.chat_completion` in `ntg_llm/service.py` accepts system messages | Ready; Agent owns message construction. |
| Supply task and context | The same method accepts nonempty text user messages | Ready; serialize task/context into one user message. |
| Require predictable output | `response_schema` and `validate_response` in `ntg_llm/schemas.py` | Ready for named Pydantic object schemas, nested models, lists, and enums. |
| Return typed results | `ChatCompletion` in `ntg_llm/responses.py`; `message.parsed` in the client | Ready; Agent consumes the validated object. |
| Preserve failure diagnostics | `RoutingError`, `CompletionService._run`, `diagnostics.failed_attempts` | Ready; Agent must preserve these rather than add another retry loop. |
| Keep calls independent | Message lists and schemas are constructed per request; SQLite selection is transactional | Ready; Agent must not store conversation history between runs. |
| Use tools | The client accepts text messages with system/user/assistant roles; no tool-call interface exists | Separate future work. An empty tool set is sufficient for the first Coder. |
| Execute or verify generated code | No execution sandbox exists | Generation and Python syntax checking are feasible; execution and behavioral correctness are outside this implementation. |
| Operate several cooperating agents | No scheduler, messaging bus, or shared agent memory exists | Not required for this milestone. |

The full existing test suite passed: **69 tests**. These use synthetic providers
and a live-HTTP blocker. A separate in-memory feasibility probe uses `FakeRouter`,
a temporary SQLite database, system instructions, JSON-encoded task/context, and
a `CodeOutput` model through the actual `LLMClient` interface. It does not load
credentials, modify the production database, or contact a provider.
The probe passed: instructions/context were preserved, typed output and complete
JSON serialization succeeded, and exactly one mocked upstream attempt occurred.

## Recommended design

Use composition: `Agent` owns its role and input preparation, and delegates to the
existing `LLMClient`. The Coder is a configured instance, not a specialized routing
implementation. There is one completion invocation per `run`; the existing router
can make its configured upstream retries and fallbacks inside that invocation.

```text
Task + optional context
    -> Agent (name, instructions, output model)
    -> existing LLMClient
    -> existing router and provider adapter
    -> schema validation
    -> AgentResult (agent name, typed output, model, diagnostics)
```

Proposed interface:

```python
agent = Agent(
    name="chosen-role",
    system_prompt="Role-specific instructions",
    llm=LLMClient(),
    response_model=YourOutputModel,
)
result = agent.run(task, context=optional_text)
```

`Agent` is general-purpose. `create_coder_agent(llm)` provides the first runnable
configuration, with a Python engineering role and `CodeOutput(code, explanation)`.
The coding request comes from the caller; no calculator, Fibonacci, filename, or
task-planning workflow is built into the Agent class.

## Alternatives considered

1. **Composition over the current client — recommended.** Reuses validation,
   routing, safe diagnostics, and offline test seams with a small new interface.
2. **Subclass the client for each role.** Couples role instructions to transport
   behavior and makes later roles harder to configure independently.
3. **Introduce an agent framework and tool loop.** Requires tool execution,
   lifecycle, and dependency decisions beyond the first-agent goal, while the
   current client already supplies the needed completion behavior.

## Reliability boundary

For this milestone, reliable means valid input handling, stable role/context
construction, validated structured output, isolated runs, preserved diagnostics,
and bounded behavior inherited from the router. The Coder additionally rejects
empty or syntactically invalid Python using `ast.parse`, without executing code.

Syntax validity does not establish semantic correctness. Mocked tests establish
the application's behavior, not live provider availability or real model coding
quality. Any live evaluation needs separate authorization under the existing
no-live-calls constraint.

Schemas currently require every declared field and reject open dictionaries.
Custom validators can run more than once through the current client; the proposed
Coder validators will only check values and return them unchanged. They must be
pure and idempotent. Context is user-supplied data, not a new system message; this
message-role distinction is not a guarantee against prompt injection.

## Deliverable

The concrete implementation sequence and acceptance tests are in
[`../plan/feature-agent-milestone4-v1.md`](../plan/feature-agent-milestone4-v1.md).
