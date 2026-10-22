# Milestone 6 feasibility assessment

Date: 2026-10-22. Status: ReAct feasibility assessed; implementation completed and verified.

## ReAct reassessment

The user requested ReAct and authorized proceeding after feasibility study.
The [original ReAct paper](https://arxiv.org/abs/2210.03629) interleaves reasoning,
actions, and environment observations to revise a task trajectory. This is
compatible with the existing structured decisions and host-owned tools.

The previous `for` loop was already a ceiling, not a predetermined action plan.
ReAct governs action selection; it does not itself guarantee eventual termination.
Use a completion-driven `while` loop with explicit ReAct instructions, keeping
the existing configurable maximum as a separate guard against stalled execution.
The model can finish immediately or after any observation. When no tool calls
remain, allow a final answer that honestly reports incomplete work; reject any
additional action before execution with the existing 508 error.

Implement a ReAct-style adaptation: privately assess progress and missing evidence,
select one typed action, observe its actual result, then revise the next choice.
Do not collect verbose reasoning traces or parse free-text Thought/Action labels.
This differs from the paper's explicit reasoning-trace demonstrations; it is not
a reproduction of its experiments or evidence of equivalent performance.

Feasibility cross-check: `run_with_tools` already retains assistant decisions and
tool observations; `ToolRegistry` already validates actions; provider recovery
already surrounds each decision rather than replaying side effects. No SDK,
schema migration, provider capability change, or new dependency is needed. Add
the host-owned remaining-action count to each observation and state the initial
ceiling in the system instruction. Keep current public APIs and tool-free mode.

Alternatives considered: a framework/native-tool migration adds unnecessary
adapter work; unbounded execution removes the only deterministic stop for a model
that keeps selecting tools; a free-text ReAct parser weakens the existing typed
contract. The structured ReAct adaptation is the smallest compatible option.

Validate with observation-dependent copying, corrective missing-file recovery,
different completion lengths, final-at-limit behavior, and sequential isolation
through the real service/validation/tool stack. Mock providers can establish these
host mechanics but cannot establish improved live model reasoning or success rates.

## Milestone interpretation

The required behavior is repeated model decision -> host tool execution -> model
observation -> next decision, terminating with a validated final answer or a
bounded failure. The illustrative while loop is not an API specification.
Neither a literal `tool` message role nor native provider function calling is
required. Internal model reasoning does not need to be exposed or stored.

## Findings cross-checked against source and tests

| Required behavior | Current implementation | Existing evidence |
|---|---|---|
| Start from the caller's task | `Agent.run` constructs isolated task/context messages | `tests/test_agent.py`, `tests/test_tool_agent.py` |
| Ask the model for its next decision repeatedly | `run_with_tools` requests a schema-validated decision on each turn | `test_write_list_read_and_final_preserve_feedback_and_safe_traces` |
| Select and execute a registered tool | `ToolRegistry.build_decision_model` and `execute` validate and dispatch host handlers | `tests/test_tool_registry.py` plus real temporary filesystem actions |
| Feed execution results into the next turn | Runtime appends the assistant decision and JSON tool-result observation to local history | Feedback assertions in `tests/test_tool_agent.py` |
| Finish without another tool request | `action=final` returns typed `ToolRunOutcome`, then `AgentResult` | Generic final-contract and immediate-final tests |
| Bound runaway execution | At most N tool attempts and N+1 model turns; extra action is rejected before execution | Budget exhaustion and final-at-budget tests |
| Let the model react to expected failures | Expected tool errors become observations | Missing-file observation test and new adaptive list/read/write recovery tests |
| Preserve retry and side-effect boundaries | Router retries a model request; runtime executes accepted actions afterward | Retry-without-write-replay and partial-effect tests |
| Keep runs isolated | History, traces, and call IDs are local variables | Concurrent run-isolation test |

The core loop already shipped as part of Milestone 5. A second loop or a new
agent framework would duplicate working behavior. No new production subsystem
is needed for the supplied Milestone 6 goal.

## Compatibility and feasibility

`LLMClient.chat.completions.create` accepts structured text completions.
`CompletionService.chat_completion` accepts system/user/assistant text roles and
rejects native tool messages. The existing runtime therefore encodes tool results
as user-message JSON observations and identifies them as data in the system
instruction. That transport satisfies the requested feedback behavior while
preserving all current provider adapters. Native tool calling would expand the
facade, response contracts, normalization, and adapter scope without advancing
this milestone's stated goal.

Nonempty tool registries enable the loop; absent/empty registries preserve the
single-call behavior. `max_tool_calls` is already the public execution bound.
Provider retries may produce more upstream HTTP attempts than model decision
turns. This is intentional and separate from tool execution.

Existing scripted tests verify history and real actions, but a fixed sequence
alone cannot show that a later choice depends on an observed value. The useful
remaining work is an explicit ReAct policy, observation-dependent acceptance
scenarios, recovery evidence, sequential isolation, and Milestone 6 documentation.
These are verification gaps, not demonstrated runtime defects.

## Recommended scope

Adapt the runtime to an explicitly prompted, completion-driven ReAct cycle.
Retain the registry, schemas, provider recovery, CLI options, and public
result contracts. Add deterministic integration tests through the real client,
completion service, validation, registry, and temporary workspace. Add usage and
acceptance documentation, then update the README to identify Milestone 6.

Do not add planning agents, shell execution, code execution, persistent memory,
resume/replay, streaming, native function calling, or automatic invalid-output
repair. None is necessary for the supplied goal. Final structured output is the
model's completion signal; the host does not independently prove task correctness.

## Validation boundary

Baseline checked on 2026-10-22: `python -m pytest -q` returned **195 passed,
1 skipped** in 56.13 seconds. `python -m ruff check .` passed and
`python -m ruff format --check .` reported 53 files already formatted. The
existing native-symlink test skips when this Windows host cannot create links.

Post-implementation verification: **203 passed, 1 skipped** in 59.52 seconds;
the focused tool-agent suite passed 25 tests. Ruff lint, formatting, and CLI help
passed. New tests verify adaptive trajectories in both response modes, variable
completion lengths, sequential isolation, and final output at zero remaining
tool attempts. The same Windows native-symlink limitation accounts for the skip.

The full offline suite and Ruff were run before and after the changes. Tests
block live HTTP and use temporary SQLite state and filesystem roots. Mocked
provider tests establish host behavior, not live model judgment or universal
provider schema compatibility. A live smoke test is optional and is not a
completion requirement for this plan.

Git resolves to an ancestor outside NTG, and NTG is untracked in that repository.
Avoid broad staging or commits that could capture unrelated user files. Planning
and implementation changes should remain scoped to this directory.

See [the concrete implementation plan](../plan/feature-agent-loop-milestone6-v1.md).
