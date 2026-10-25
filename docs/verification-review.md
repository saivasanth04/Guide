# Verification reliability review

Reviewed the supplied live-execution log, the Coder/Tester/orchestrator, the saved
application, and live sandbox behavior. No stronger-model claim is justified by
this evidence alone; both implementation defects and orchestration defects exist.

## Observed causes

- The actual entry file is `expense_tracker/tracker.py`. The Tester guessed
  `expense.py` and incorrect parent-relative paths, producing setup errors rather
  than evaluating application behavior.
- Some model responses claimed changes or passing tests without corresponding
  write/execution observations. A separate cloud-model smoke run reproduced such
  unsupported claims and hit its 180-second external limit. This was not a PASS.
- Verification compared every historical execution, so a corrected harness could
  still be rejected because its earlier version failed.
- Missing evidence and broken verification were routed back as implementation
  defects, wasting Coder repair cycles and encouraging edits to independent tests.
- Remote-only runtime data could become protected inputs on subsequent commands.
- The generated application has real numeric edge-case defects: live execution
  showed `NaN` raising `decimal.InvalidOperation` and `Infinity` being saved with
  exit code zero. The generated application was examined in an isolated copy;
  the user's saved application/data were not modified by these probes.

## Implemented controls

- Supply actual project-file inventory and sandbox working directory. Test-file
  creation returns explicit local/remote paths. No task-specific path substitutions.
- Allow at most two internal verification corrections after the initial attempt.
  Wrong harnesses, malformed verdicts, and unsupported PASS claims remain failures.
- Evaluate the latest execution of each saved suite; retain older executions as
  superseded evidence, including test-file hashes. An unrerun failing suite still
  blocks PASS, and source changes cannot be superseded by a later success.
- Distinguish implementation, verification, and infrastructure failures. Unresolved
  verification stops explicitly instead of requesting inappropriate production repairs.
- Protect synchronized inputs, not remote-only runtime artifacts. Existing local
  data remain protected; tests should use temporary data or disposable copies.
- Require successful Coder sandbox self-verification after its latest filesystem
  edit before handoff. One bounded continuation is allowed if it is missing.
- Show actual Tester execution evidence and accepted verdicts rather than raw model
  success claims. Enable sanitized provider/model/attempt-count traces in the entry point.

## Feasibility and limits

These changes reuse the existing agents, routing, MCP, synchronization, and E2B
implementation. They improve reliable decisions and failure ownership without
increasing the three-repair default or inventing successful outcomes. They cannot
make an incapable or noncompliant model solve every task. Repair exhaustion can
still be legitimate when a real implementation defect remains.

The next model-quality decision should use provider/model traces and a fixed task
set scored on actual edits, execution, assertions, and completed requirements.
Changing retry counts or trusting prose PASS claims would hide defects rather
than fix them. The cloud smoke run does not establish live model success; the
deterministic regressions use controlled provider decisions with real MCP/E2B.

Regression coverage includes corrected harnesses, no-evidence rejection, source
mutation rejection, repeated runtime writes, immediate PASS, actual product repair,
and strict repair-limit termination. See the test files and README for commands.
