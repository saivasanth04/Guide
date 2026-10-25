# Coding workflow reliability fixes

The direct live review is recorded in `live-run-2026-10-25.md`. This follow-up
fixes execution control and evidence handling in the existing Coder/Tester
architecture. No production language, generated filename, or task-specific test
command is embedded in the orchestration.

## Changes

- `ntg_llm/agent_protocol.py` requests typed tool decisions through the existing
  NTG router and converts validated arguments into LangChain actions. Tool
  arguments and Tester verdicts are actual JSON objects, not JSON-encoded
  strings. Native provider function-call APIs are not required.
- Completion is unavailable before Coder self-execution or Tester saved-file
  execution. Coder self-verification must follow its latest filesystem edit.
  Negative probes keep their real exit codes; they do not erase an earlier
  successful check. Actual Coder observations, including failures, accompany
  the handoff. The independent Tester determines correctness.
- The Tester writes persistent test files and supplies a runner argument array
  and sandbox working directory. The executor stages saved files in an isolated
  namespace inside cwd and appends the relative test path, supporting file-based
  and module-oriented runners. It quotes every argument and rejects cwd outside the
  sandbox workspace. No model-authored parent-relative test-file interpolation.
- Tester PASS requires successful saved-file execution, unchanged test hashes,
  intact synchronized project sources, and a coverage review against frozen
  acceptance criteria quoted from the original task before source inspection.
  Criteria distinguish behavior, artifacts/documentation, and the final response.
  Approved suites remain saved and are rerun with supplemental suites; missing
  assertions trigger bounded expansion against only unmet criteria. Earlier
  executions of the same suite remain visible as superseded evidence; a passing
  different file does not discard an unrerun failure.
- Tester plans contain complete test sources, runner/cwd settings, and optional
  support files for test-only dependency manifests or helpers. The engine saves
  and executes every planned suite; a model cannot substitute a prose PASS claim.
  The existing E2B installer handles test-only dependencies automatically without
  changing production manifests. Missing runners/packages are verification setup
  errors that the Tester can correct within its bounded correction attempts.
- The Coder's filesystem writers reject changes to independent `.agent_tests`
  artifacts. It retains other filesystem edits, sandbox execution, diagnosis,
  debugging, and self-verification.
- Default new-project runs get a fresh directory and authoritative inventory.
  Set `NEW_WORKSPACE_PER_RUN = False` to work directly in `WORKSPACE`, or supply
  `main(workspace=...)`. Existing-project work uses the same entry point.
- Sandbox commands use Bash `errexit` and `pipefail`. Expected errors should be
  handled with explicit assertions or conditionals; actual exit codes and the
  executed wrapper remain in evidence.
- Routing diagnostics retain validation/provider failure stages and exception
  types plus safe validation paths/keywords, including when the global budget
  expires. Provider values and credentials are excluded. Validation errors are no
  longer mislabeled as transient provider outages. Per-deployment retries remain
  zero and the 30-second provider / 60-second routing defaults are unchanged.
- Readable multiline context replaces nested escaped JSON for code, tool results,
  and tracebacks. Optional role-specific models and reasoning settings use the
  existing abstraction. Gemini reasoning settings use the installed SDK's native
  request translation, bypassing stale model-capability metadata that rejected
  supported settings before any upstream request.
- Three configured Coder repairs remain the default. Verification/infrastructure
  failures stop explicitly; they do not consume implementation repairs.

## Validation

Final local regression run: **91 passed, 12 optional live checks skipped**.
`python -m ruff check .` and `git diff --check` also passed.
The final real-E2B workflow regression run passed **10/10 scenarios**, including
automatic installation of a Tester-only dependency manifest. Model responses in
these regression scenarios are controlled local HTTP responses, not cloud decisions.

Local tests cover structured decisions, nested MCP arguments, typed verdicts,
coverage corrections, independent-file protection, negative-probe handoff,
failure-stage preservation, actual-evidence acceptance, and strict repair limits.

Real-E2B regressions exercise immediate PASS, FAIL/repair/PASS, three-repair
exhaustion, source-mutation rejection, runtime-data isolation, corrected suites,
shell failure propagation, and saved test/cwd paths containing spaces. Their
provider decisions are controlled locally; their MCP operations and E2B commands
are real. Autonomous cloud runs are tracked separately in the live logs.

## Observations during live debugging

A pilot completed with a real saved-file execution, but its assertions missed
important behavior. The added coverage audit then detected missing non-finite
input checks and preservation of existing records. A later run exposed the
model's incorrect `../` prefix around the tool's absolute test path; the runner
interface removes that source of ambiguity. Another run exposed an overly strict
completion gate after expected negative probes. These runs were retained as
failures rather than counted as successful verification.

Raw logs are retained locally under `artifacts/live-review-2026-10-25/`.
Provider availability and model-assisted coverage judgments remain practical
limits; a process exit of zero alone cannot establish complete correctness.

## Final direct entry-point result

`python -u coder_agent.py` completed with real cloud model decisions, MCP filesystem
operations, and E2B execution. Its fresh workspace is
`C:\Users\perur\Desktop\workspace\runs\20261025-030748-cd6cf002`.
The Tester saved `test_expense_tracker.py`, executed six groups of assertions,
accepted all 12 frozen criteria, and returned PASS with exit code zero and no
source mutations. The workflow stopped immediately with `repair_attempts: 0`.
The normal task now explicitly requires rejection of amounts that round to zero,
resolving the ambiguous sub-cent policy identified in the original review.

A separate autonomous run also completed with 11/11 criteria accepted. Failed
runs were retained, including a real application defect followed by Coder repair,
an execution that exhausted exactly three repairs, and provider-budget failures.
They are not counted as successful complete runs.

The opt-in autonomous cloud repair test deliberately injects a wrong conversion
offset after Coder self-check. All decisions, MCP actions, and E2B results are
real. Initial attempts exposed provider-budget exhaustion and an undeclared
test-runner dependency. Those failures led to the test-only dependency correction
above. Its final report is `artifacts/cloud-repair-result.json`; raw logs distinguish
these autonomous decisions from the controlled-provider E2B regression scenarios.

The final autonomous repair scenario **passed**: Tester detected three failed
conversion assertions, returned an implementation FAIL, Coder repaired the offset
once, and the independent Tester retested successfully. The final report records
`status: PASS`, `repair_attempts: 1`, and verification history `FAIL, PASS`.
The opt-in cloud test passed in 501.76 seconds; this timing includes initial
implementation/self-check, deliberate fault injection, test planning, repair,
retesting, cloud failover, and sandbox lifecycle. The 60-second routing budget is
per LLM request, not an overall workflow deadline.
