---
goal: Implement a structured ReAct cycle for Milestone 6
version: 2.0
date_created: 2026-10-22
last_updated: 2026-10-22
owner: NTG
status: Completed
tags: [feature, agent-loop, milestone6, verification]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Milestone 5 supplies the tools and feedback transport. The user authorized a
ReAct feasibility study followed by implementation. Version 2 replaces the earlier
verification-only proposal with an explicit ReAct policy and a completion-driven
runtime. Preserve typed contracts and the configurable host execution ceiling.

## 1. Requirements & Constraints

- **REQ-001**: A run can perform multiple registered tool actions, observe each result, and return validated final output through the existing `Agent.run` API.
- **REQ-002**: Acceptance tests must demonstrate a later tool argument and final output derived from an earlier observation rather than a predetermined response sequence.
- **REQ-003**: Expected tool failure must permit an observation-dependent corrective action within the existing budget.
- **REQ-004**: Preserve immediate final output, N tool attempts plus one final decision opportunity, no execution over budget, provider recovery without tool replay, and isolated per-run history.
- **SEC-001**: Use temporary workspaces/databases and synthetic providers; retain the suite's live HTTP prohibition and existing secret-free diagnostics.
- **CON-001**: Keep `max_tool_calls`, `AgentResult`, JSON decision schemas, and current CLI flags compatible. Do not add native tool messages or dependencies.
- **CON-002**: Adapt the existing runtime rather than adding a second loop or framework. Use private progress assessment, typed actions, observations, and model-selected completion; retain the action ceiling as a separate guard.
- **GUD-001**: Describe model decisions and observations without requiring exposed chain-of-thought or claiming independent proof of task correctness.
- **PAT-001**: Reuse the `FakeRouter`, `CompletionService`, `RotationStore`, `LLMClient`, and filesystem registry patterns from `tests/test_tool_agent.py`.

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: Establish deterministic acceptance evidence for the existing runtime.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Update `run_with_tools` in `ntg_llm/tool_runtime.py` to use a completion-driven `while` cycle. Prompt private progress assessment, evidence-based next actions, error correction, and honest final output. Include the initial ceiling in the system instruction and `remaining_tool_calls` in each observation. Preserve validation and pre-execution limit enforcement. Update the Agent docstring and CLI help to describe the ceiling accurately. | Yes | 2026-10-22 |
| TASK-002 | Add `react_stack` and `test_react_observations_drive_recovery_actions_and_final_output` in `tests/test_tool_agent.py`. Parametrize both response modes and recovery/no-recovery branches. Use unique input filenames/content. Select subsequent reads from observed pointers or directory entries, copy observed contents, and finish from confirmed write byte counts. Assert ordered call IDs, history, actual file bytes, turn counts, remaining budgets, and safe diagnostics. | Yes | 2026-10-22 |
| TASK-003 | Add `test_react_finishes_on_model_decision_and_sequential_runs_reset`, parametrized over zero, one, and three actions with ceiling three. Reuse an Agent for two tasks; assert private histories/counters and immediate/early/at-ceiling completion. Add `test_react_expected_failure_consumes_budget_and_allows_honest_final` to prove a failed read consumes the last attempt and still permits an honest incomplete answer. | Yes | 2026-10-22 |
| TASK-004 | After TASK-001 through TASK-003, run `python -m pytest -q tests/test_tool_agent.py`. Require new acceptance tests and existing limit, malformed-decision, partial-effect, no-replay, and isolation tests to pass. | Yes | 2026-10-22 |

### Implementation Phase 2

- **GOAL-002**: Publish accurate Milestone 6 usage and verified completion evidence. Depends on GOAL-001.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | Create `docs/milestone6.md` after TASK-004. Explain private assessment -> structured action -> observation -> reassessment -> typed final output. Include CLI and Python examples, ceiling semantics, failure handling, compatibility, and the distinction from the original ReAct paper's exposed reasoning traces. Link the primary paper, assessment, and this plan. | Yes | 2026-10-22 |
| TASK-006 | Update `README.md` after TASK-005: change the current milestone heading to Milestone 6 and add a short loop summary/link to `docs/milestone6.md`. Preserve the Milestone 5 tool-policy link and all existing API examples and provider guidance. Do not rename public options or imply tools are enabled without a registry/workspace. | Yes | 2026-10-22 |
| TASK-007 | After TASK-006, run `python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`, and `python main.py --help`. Record exact outcomes and any platform skip in `docs/milestone6.md`. Ensure help exposes existing workspace/budget options without loading provider credentials. | Yes | 2026-10-22 |
| TASK-008 | After TASK-007 passes, update this plan to Completed with actual task completion dates and update the feasibility assessment's status to implemented. Review only NTG-scoped changes. Do not broadly stage or commit the ancestor Git repository. | Yes | 2026-10-22 |

## 3. Alternatives

- **ALT-001**: Build a second standalone agent loop. Rejected because `run_with_tools` already owns the required lifecycle; duplication would create inconsistent budgets and diagnostics.
- **ALT-002**: Add native provider function calling. Deferred because the client and service accept structured text messages; it requires broader adapter changes without being necessary for the milestone.
- **ALT-003**: Only rename Milestone 5 to Milestone 6. Rejected because the user requested explicit ReAct behavior and adaptive acceptance evidence.
- **ALT-004**: Remove all bounds or parse free-text Thought/Action output. Rejected because ReAct does not guarantee termination and free-text parsing weakens typed dispatch. Use a structured ReAct adaptation with private reasoning instead.

## 4. Dependencies

- **DEP-001**: Existing Python environment with the project's dev dependencies installed; no additional packages.
- **DEP-002**: Existing typed registry, loop, JSON validation, fake provider infrastructure, and temporary SQLite fixtures.

## 5. Files

- **FILE-001**: `tests/test_tool_agent.py` — observation-dependent acceptance, corrective recovery, sequential run-isolation tests.
- **FILE-002**: `docs/milestone6.md` — new current milestone usage, lifecycle, boundaries, and verification record.
- **FILE-003**: `README.md` — current milestone and discoverability.
- **FILE-004**: `docs/milestone6-feasibility.md` — assessment and final status.
- **FILE-005**: `plan/feature-agent-loop-milestone6-v1.md` — execution status and completion record.
- **FILE-006**: `ntg_llm/tool_runtime.py` and `ntg_llm/agent.py` — explicit ReAct policy, completion-driven cycle, host budget observations, and updated docstring.
- **FILE-007**: `main.py` — clarify that the existing tool-call option is a safety ceiling.

## 6. Testing

- **TEST-001**: TASK-002 must pass in both response modes and prove copied output derives from unique observed content.
- **TEST-002**: TASK-002 must prove the missing-file error, directory listing, and read contents reach successive decisions in order.
- **TEST-003**: TASK-003 must prove variable-length completion, failure-budget feedback, and sequential history/counter isolation.
- **TEST-004**: Existing budget, invalid decision, provider retry, handler failure, generic output, and concurrent-isolation coverage must continue to pass.
- **TEST-005**: Full offline suite, lint, format, and CLI help checks in TASK-007 must pass; report platform skips explicitly.

## 7. Risks & Assumptions

- **RISK-001**: A scripted provider validates host control flow rather than live model competence. Require observed-value-dependent responses to avoid mistaking a fixed transcript for adaptive evidence.
- **RISK-002**: Existing Windows link-creation restrictions can skip native symlink tests; report this boundary without weakening path protection checks.
- **RISK-003**: NTG sits under an ancestor Git repository containing unrelated user data. Scope every modification and review to this directory.
- **ASSUMPTION-001**: The supplied milestone defines an execution loop, not independent task-success verification, persistent sessions, or arbitrary command execution.
- **ASSUMPTION-002**: Existing opt-in tools and typed final output are suitable interfaces; no new transport or budget API is required.

## 8. Related Specifications / Further Reading

- [Milestone 6 feasibility assessment](../docs/milestone6-feasibility.md)
- [Milestone 5 lifecycle and usage](../docs/milestone5.md)
- [Milestone 5 original implementation plan](feature-tools-milestone5-v1.md)
- [Current loop implementation](../ntg_llm/tool_runtime.py)
- [Current loop tests](../tests/test_tool_agent.py)
