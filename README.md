# Routed generation with a Google-style client interface

Run the complete Coder → Tester → Reviewer → repair workflow through one entry point:

```powershell
python coder_agent.py
```

Edit `TASK` and `WORKSPACE` near the top of `coder_agent.py` before running.
The task is defined in code; no command-line arguments are needed.
Each CLI run gets a fresh `WORKSPACE/runs/<timestamp-id>/` workspace, printed at
startup. Earlier generated projects stay outside the agent's allowed directory.
For a task that edits an existing project, set `NEW_WORKSPACE_PER_RUN = False`
and point `WORKSPACE` at that project. This retains the Coder's existing-project
inspection and repair capabilities through the same entry point.
Programmatic `main(workspace=...)` uses the explicitly supplied directory as-is
for continuing work on an existing project.

`CODER_MODEL`, `TESTER_MODEL` and `REVIEWER_MODEL` optionally select configured models
(all default to `router`). `AGENT_REASONING_EFFORT=low` controls supported reasoning models;
`CODER_REASONING_EFFORT`, `TESTER_REASONING_EFFORT` and `REVIEWER_REASONING_EFFORT`
override it per role.
Leave these effort settings unset for providers without reasoning support.

The library also supports direct generation calls:

```python
from ntg_llm import LLMClient

client = LLMClient()
response = client.models.generate_content(
    model="router",
    contents="Explain generative AI in one sentence.",
)
print(response.text)
```

The public method has the same keyword argument names as Google's
`client.models.generate_content(*, model, contents, config=None)`.
The implementation uses our existing provider routing, credential pool, persistent
rotation, failover and sanitized errors; it does not create a separate Google client.
Credentials stay in `.env`, with provider/model settings in `llm.json` and `models.json`.

Routing latency settings in `.env`:

```dotenv
RETRIES_PER_DEPLOYMENT=0
PROVIDER_REQUEST_TIMEOUT=30
COMPLETION_TIME_BUDGET=60
COMPLETION_MAX_DEPLOYMENTS=all
```

Each LLM routing request has one monotonic deadline. Each provider attempt receives
the smaller of its timeout and the remaining routing budget. Failed, timed-out,
overloaded, invalid-response, and rate-limited deployments immediately fail over
without retry sleeps. The legacy retry setting is accepted for configuration
compatibility; it no longer enables repeated attempts against a failed deployment.
429 responses enter deployment cooldown using `Retry-After`, or 30 seconds if the
header is absent or invalid. Access-enforcement stops remain terminal.

A bounded caller wait protects against synchronous SDKs that ignore timeouts.
Such a call may finish in a daemon thread after the caller moves on; its late
result cannot update the routing ledger. The deadline applies per LLM request,
not to the entire multi-turn agent run, filesystem calls, or initial imports.
Budget exhaustion raises a sanitized 504 with attempt diagnostics. Round-robin
selection and specific-model scoping remain unchanged.

Install coding-agent dependencies with `python -m pip install -e ".[dev,agent]"`.
The coding workflow runs a fully capable Coder, independent Tester, then independent Reviewer:

```powershell
python coder_agent.py
```

The Coder retains filesystem inspection/editing, E2B execution, debugging, repair,
and self-verification. The Tester receives only read-only MCP filesystem tools
and E2B execution, chooses behavioral checks from the task/project, and verifies
in a fresh sandbox each round using the same executor and dependency installer.
Temporary runtime data belongs outside the project. Existing source changes during
Tester commands invalidate the result. The Coder's session sandbox remains reusable.

The Tester returns a typed `TestPlan` containing complete, separate test files,
runner settings, and requirement coverage. The engine saves and executes every
planned file automatically through the restricted writer and existing E2B executor;
the model cannot skip execution or substitute a prose PASS claim. Files remain under
`WORKSPACE/.agent_tests/<verification-id>/` for inspection, and their paths appear
in the structured Tester report. The Tester chooses a runner executable/flags
array and a sandbox working directory. The engine stages the verification files
under an isolated namespace in that directory and appends the relative test path.
This supports file-based and module-oriented runners. For example, a runner
can be `["node", "--test"]` with a project directory as `cwd`; languages are not
hardcoded. Inline commands alone cannot establish PASS.
Optional `support_files` hold test-only dependency manifests and helpers beside
the saved tests. The existing dependency installer installs declared packages
automatically; production manifests are not changed by the Tester.
Existing project-file modifications/deletions invalidate verification; newly
created runtime data files are recorded as artifacts instead of source changes.
Tests should use temporary data or disposable project copies to protect existing
data, and assert expected application error codes inside the test suite. A passing
suite returns zero even when it verifies that an invalid application input returns one.

`[Workflow] Authoritative Tester verdict` shows the evidence-validated decision;
an LLM's raw `Final Answer: PASS` is only a claim. Progress messages display each
repair number and announce completion or repair-limit exhaustion.

The Tester receives a real project-file inventory and sandbox working directory.
Before inspecting the implementation it freezes acceptance criteria quoted from
the original task. Coverage corrections address only unmet criteria. Previously
approved suites remain saved and are rerun alongside supplemental suites, so
review cannot indefinitely expand requirements after passing tests.
It can correct verification mistakes twice internally. Successful reruns supersede
earlier attempts of the same suite (history and test hashes remain available);
other failing suites and source mutations still block PASS. Verification or
infrastructure failures stop explicitly instead of consuming production repair
cycles. Actual application failures still follow the bounded Coder repair loop.
The Coder must supply successful E2B self-verification after its latest file edit;
one bounded self-verification continuation is allowed before stopping without proof.
The entry point uses schema-validated action decisions through the existing
router. A final answer is unavailable to the Coder until a sandbox self-check
passes after the latest edit. A Tester planning response is a proposal; its
authoritative verdict is produced only after saved-file execution and review.
Final verdicts still undergo independent evidence validation;
the transport guard does not establish that a suite covers every requirement.
Negative-input probes retain their actual exit codes and are forwarded alongside
successful Coder checks to the Tester. They do not erase earlier self-verification
after the latest edit; the independent suite determines which failures are expected.
Before accepting PASS, the live Tester audits the saved test source and execution
evidence against the original task. Missing behavioral assertions are sent back
for test expansion within the same bounded verification corrections. This is a
model-assisted coverage review, not a mathematical guarantee of complete coverage.
The default structured coverage audit must cite exact test/inspection/reply evidence
for each claimed covered criterion. The engine rejects ungrounded quotations,
missing citations and inappropriate evidence types. Audit records are retained in
`coverage_audits` and under `.agent_tests`; real quotations still require judgment
about whether the assertions are sufficient.
Citation-only mistakes receive at most two audit corrections against the same
executed tests before they can remain a gap; they do not trigger test regeneration
or consume Coder repairs. Common block indentation/line endings are normalized
without ignoring relative code structure. The catalog includes bounded observed
text artifacts and preserves read-only inspections across planning corrections.
Saved tests changed after execution must be rerun. Sandbox commands use Bash
`errexit` and `pipefail`, so trailing `echo` commands and pipeline consumers do not
hide failures. Handle expected failures with assertions or explicit conditionals.
Entry-point logs include routed provider/model and attempt count. Tester logs show
actual execution observations, not unvalidated model PASS claims.
LLM traces also identify the role, elapsed seconds and success/failure. Sandbox
observations include elapsed seconds for the entire executor call; Reviewer logs
its read-only inspection tools and arguments. Before writing a structured TestPlan,
the engine rejects duplicate/overlapping artifact paths, parent traversal and
runner arguments that repeat the test filename. Test processes receive absolute
`NTG_WORKSPACE_ROOT` and `NTG_TEST_CWD` environment values for locating source
before changing into temporary directories.
See [the reliability review](docs/verification-review.md) for findings and limits.
See [the supplied-run review](docs/log-review-2026-10-25.md) for reproduced false
confidence, measured outcomes and remaining model-quality limits.
See [the failed-flow analysis](docs/flow-review-2026-10-26.md) for per-stage failure
ownership, citation recovery and validation of the latest repairs.

The Reviewer runs only after authoritative Tester PASS and uses the existing LLM
router and agent protocol with read-only MCP filesystem tools. It inspects actual
project content for requirement completeness, correctness missed by tests, design,
maintainability, unnecessary complexity and visible security/reliability problems.
It has no editing, test-writing or command-execution tools and does not rerun the
Tester's behavioral suites. It receives the original task, latest Coder summary
(a claim, not proof), full Tester result and execution evidence, and saved test paths.
Minor suggestions permit APPROVE; REJECT requires at least one major or critical
issue with concrete source evidence. Invalid review output, missing content
inspection, iteration exhaustion or routing failures stop the workflow as review
failures instead of requesting production changes.

Set `MAX_REPAIR_ATTEMPTS=3` in `.env` (default). This permits the initial implementation plus at most
three Coder repairs and four Tester rounds; individual test commands do not
increment the counter. Tester implementation failures and Reviewer rejections
share this budget. Every repair goes through Tester again before any new review.
Coder receives structured review issues and the Tester PASS evidence on review
rejection, with explicit instructions to investigate, preserve requirements and
leave independent test artifacts intact. Set zero to disable repairs. Exhaustion returns FAIL with
the latest evidence and a nonzero CLI exit status. Each agent also retains a
25-iteration limit, and the existing LLM request budgets remain in force.

Final results are JSON containing status, repair count, Coder summary, latest
Tester report, verification history, latest current-round `reviewer` (or null),
and `review_history`. Historical review results cannot approve a later implementation.
Programmatic orchestration now requires `run_workflow(task, coder, tester, reviewer, ...)`;
Reviewer implements `review(task, coder_output, tester_result)`.
Tester evidence comes from actual tool
observations, including command, exit code, stdout/stderr, sandbox identity,
setup/install errors, and source changes. PASS requires executed, successful
checks and Reviewer APPROVE; missing evidence, malformed verdicts, or failed commands cannot pass.
Tester PASS alone can appear in a final workflow FAIL when review rejects or fails.
`failure_stage` identifies the failed agent. The result contract itself rejects
PASS unless both authoritative verdicts succeed. Reviewer source-access failures
stop explicitly instead of manufacturing implementation rejections.
See [Reviewer architecture and validation](docs/reviewer-workflow.md).

Deterministic orchestration tests: `python -m pytest -q tests/test_agent_workflow.py`.
Live workflow scenarios (real MCP, E2B, and the LLM adapter/router with controlled
local provider responses) cover immediate PASS, FAIL/repair/PASS, repair exhaustion,
and source-mutation rejection:

```powershell
$env:NTG_LIVE_E2B='1'
python -m pytest -q tests/test_workflow_live.py
```

For autonomous real-cloud decisions with real MCP/E2B, including a deliberately
injected implementation fault that must be detected and repaired:

```powershell
$env:NTG_LIVE_CLOUD='1'
python -m pytest -q -s tests/test_workflow_cloud.py
```

This opt-in scenario consumes configured provider/E2B services and can fail on
upstream availability. It writes its actual workflow report to
`artifacts/cloud-repair-result.json`.

For sandbox execution, set `E2B_API_KEY` in `.env`; `E2B_TEMPLATE` is optional.
The agent shares its workspace with the E2B executor. Source files sync before
each command, and the sandbox persists until agent cleanup. Remote-only artifacts
and dependencies remain between calls; sandbox edits are not downloaded locally.
Before execution, dependencies install automatically for discovered Python
(`requirements.txt`, otherwise an installable `pyproject.toml`) and npm
(`package.json`) projects, including subdirectories. npm lockfiles select `npm ci`;
otherwise `npm install` is used. Successful installs are reused until dependency
files change. Each installation step has a 300-second timeout; failures stop the
requested command and return installer output with `stage=dependency_installation`.
The tool description provides its Linux working directory and synchronization limits.
Run the optional live MCP/E2B check in PowerShell with
`$env:NTG_LIVE_E2B='1'; python -m pytest -q tests/test_e2b_sandbox.py`.
The tests include real local HTTP transport and an end-to-end ReAct run using the
MCP filesystem server (requires Node.js/npx). These require no cloud credentials.

- `model="router"` rotates across eligible configured deployments.
- A specific model, such as `model="gemini-3.8-flash"`, rotates only among matching
  configured deployments/keys. It never silently switches to another model.
- `contents` accepts text, lists of text/text parts, and user/model Content dictionaries.
  Native SDK text Content/Part objects are accepted when that SDK is installed.
- `config` accepts a dictionary or native SDK GenerateContentConfig object.
- `response.text` is the generated text; `response.model_version` identifies the
  model actually used. Routing evidence remains in `response.diagnostics`.

```python
response = client.models.generate_content(
    model="router",
    contents="Explain generative AI in one sentence.",
    config={
        "system_instruction": "Answer clearly and briefly.",
        "temperature": 0.2,
        "max_output_tokens": 128,
    },
)
print(response.text)
```

Supported generation settings: system_instruction, temperature, top_p, top_k,
max_output_tokens, seed, stop_sequences, presence_penalty, frequency_penalty,
and candidate_count=1. Settings are validated and forwarded to the selected
provider; availability can still depend on that provider/model.

For structured output, use response_mime_type="application/json" and optionally
response_schema=YourPydanticModel. `response.parsed` then contains the validated
model; without a schema it contains the decoded JSON value. Default text generation
returns plain text directly and does not require an Answer JSON envelope.

This facade currently covers synchronous text generation. Multimodal parts, tools,
streaming, multiple candidates and other unsupported config fields are rejected;
it is not a complete replacement for every Google SDK feature. Raw schema dicts
and Google Schema objects are not supported as response_schema; use a named
Pydantic model. The existing `client.chat.completions.create(...)` and
`structured_completion(...)` APIs remain compatible.

Install with `python -m pip install -e ".[dev]"`. Test with
`python -B -m pytest -p no:cacheprovider -q` and
`python -B -m ruff check --no-cache .`.

Reference: [official Google Python SDK](https://github.com/googleapis/python-genai#generate-content).
