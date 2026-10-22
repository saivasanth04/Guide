---
goal: Add sandbox-backed Tester feedback and a bounded LangGraph Coder repair workflow
version: 1.0
date_created: 2026-10-22
last_updated: 2026-10-22
owner: NTG
status: Planned
tags: [feature, tester, sandbox, langgraph, milestone9]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

Implement Milestone 9 over the current Milestone 6 LangGraph application. Preserve
the existing direct and Coder-only APIs. Add explicit project verification using
real sandbox execution, evidence-grounded Tester feedback and bounded repairs.
This file is a plan, not a claim of implementation. Repository assessment and
Docker availability are recorded in [feasibility](../docs/milestone9-feasibility.md).
Paths below are relative to the repository root.

## 1. Requirements & Constraints

- **REQ-001**: Verify actual files written by the Coder, identified by a SHA-256 snapshot manifest. Never treat syntax validation or the final code string as execution evidence.
- **REQ-002**: Implement an outer LangGraph StateGraph with coder -> sandbox -> tester and a conditional tester -> coder edge. End on selected-check success, exhausted repairs or infrastructure/policy failure.
- **REQ-003**: Add a distinct TesterAgent service and a reusable tool-free Agent[TesterFeedback] using the same LLMClient. The host establishes pass/fail; the model supplies only failure interpretation and repair advice.
- **REQ-004**: Preserve the original task, fixed test profile and latest execution evidence for every repair. Keep host-owned tests outside the writable Coder root and unchanged across attempts.
- **REQ-005**: Return typed status, verification kind, final Coder result, snapshot identity, execution reports, Tester feedback and repair count. Distinguish script success from test-suite success.
- **CON-001**: max_repair_attempts defaults to 3; require type(value) is int and 0 <= value <= 10. The initial generation is excluded. Total Coder and execution calls are each <= 1 + max_repair_attempts.
- **CON-002**: Keep max_tool_calls on each Coder run. Set outer recursion_limit=3*(max_repair_attempts+1)+1 for the three-node graph; no node retry policies or checkpoint/resume behavior. Route by explicit attempt state, not a model claim.
- **CON-003**: Initial execution profiles are script and unittest. Commands are fixed host argv using the approved Python interpreter; no arbitrary command strings, shell=True, pip installation or automatic command discovery.
- **CON-004**: Default limits: 30 seconds execution, 10 seconds per Docker control operation, 32 KiB retained per stdout/stderr stream, 256 MiB memory with equal memory+swap cap, 1 CPU, 64 PIDs, 64 MiB /tmp, 1,000 snapshot files, 10 MiB total and 256 KiB per file. Host arguments may lower/raise validated positive values; the model cannot change them.
- **SEC-001**: Require a reachable local Linux Docker engine and an existing sha256 image ID. No implicit image pulls, daemon start, privileged mode, host network, host environment forwarding, home mount or Docker socket mount. Fail closed before any Coder call when prerequisites fail.
- **SEC-002**: Run as UID/GID 65534:65534, with network none, read-only root, cap-drop ALL, no-new-privileges and bounded tmpfs. Bind only a read-only sanitized snapshot and read-only trusted tests. Container output logging uses driver none; capture attached output through bounded pipes.
- **SEC-003**: Snapshot only regular .py files plus host-explicit resource paths. Reject links/reparse points/hard links, traversal, absolute or alternate-stream paths and protected credentials/state paths. Never recurse through protected directories; use existing FilesystemTools policy through a shared checked snapshot method. External tests must be disjoint from the Coder root.
- **SEC-004**: Treat stdout/stderr as untrusted data, cap bytes during reading, strip terminal control sequences and normalize staging paths. Keep raw streams out of ordinary routing diagnostics and disable graph/model trace uploads. Report sanitized bounded excerpts as explicit execution evidence; do not promise arbitrary source secrets can be detected.
- **SEC-005**: Stop the container on timeout/output overflow, confirm termination and remove it in finally. Only clean up the uniquely named container and temporary paths created by this run. Cleanup uncertainty stops repairs.
- **PAT-001**: Keep filesystem overwrite opt-in. Verified mode requires --allow-overwrite when max_repair_attempts > 0; no implicit escalation by the Coder. Existing Coder-only behavior stays unchanged.

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: Define exact contracts and preflight behavior. Tasks are sequential unless stated otherwise. Completion: contract tests reject invalid policy before any provider or process call.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | Add ntg_llm/testing_models.py with strict closed models: ExecutionPolicy (CON-004 defaults and immutable image_id), TestProfile (kind script/unittest, entrypoint or external tests_root, explicit resource_paths), ExecutionReport (run_id, snapshot_id, kind, outcome passed/failed/timeout/output_limit/resource_limit/no_tests/infrastructure_error, exit_code optional, duration_ms, stdout/stderr excerpts, truncation flags, tests_run optional, cleanup_confirmed, safe error_code), TesterFeedback (summary, evidence references to report fields, suggested_changes), and ProjectRunResult (status passed/repair_exhausted/blocked, repairs_used, coder_result optional, reports, feedback, diagnostics). Enforce all budget types/ranges and mutually exclusive profile fields. | No | — |
| TASK-002 | Add ntg_llm/sandbox.py with SandboxRunner protocol and DockerSandboxRunner.preflight(policy, profile, workspace). Validate path syntax/containment (the script may be created by Coder later), existing external test directory, local Docker context, Linux daemon and immutable local image availability with shell=False and bounded subprocess calls. Reject missing engine/image and invalid profile before LLMClient construction in CLI. Dependency: TASK-001. | No | — |

### Implementation Phase 2

- **GOAL-002**: Implement isolated execution and trustworthy run provenance. Depends on Phase 1. Completion: runner executes a fixture under the specified policy and bounds failure/cleanup.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | Add FilesystemTools.snapshot(resource_paths, destination) in ntg_llm/tools/filesystem.py and snapshot support in ntg_llm/sandbox.py. Reuse checked paths and link policy, include .py plus explicit resources, enforce counts/bytes before copying, hash bytes actually copied and return sorted relative-path/hash manifest. Preserve existing tool behavior. Stage external trusted tests separately; reject overlapping test/workspace roots. Dependency: TASK-002. | No | — |
| TASK-004 | Implement DockerSandboxRunner.run(snapshot, profile) in ntg_llm/sandbox.py. Keep Docker create/start output identifiers separate from project stdout; create unique container with SEC-002 flags, approved image ID, working directory /workspace and interpreter entrypoint. Script argv executes the validated relative entrypoint with -B; unittest argv executes a bundled trusted harness outside /workspace with /workspace explicitly on import path. Start attached with stdout/stderr readers that drain fixed-size binary chunks, enforce limits during reading, and never use unbounded communicate or Docker disk logs. Inspect host-observed exit/OOM state, enforce deadline, kill/remove on every exit path and verify removal. Classify Docker create/start/inspect errors as infrastructure errors independent of Python exit numbers. Dependency: TASK-003. | No | — |
| TASK-005 | Add ntg_llm/testing_harness.py for stdlib unittest discovery from the read-only external suite; report testsRun and failures/errors, use nonzero exit for zero tests, failures or errors. Keep its machine result in a bounded dedicated artifact copied from /tmp before removal; ignore report-like stdout. A script success is process completion only. Treat missing/malformed harness result as failed verification, never pass. Dependency: TASK-004. | No | — |
| TASK-006 | Add tests/test_sandbox.py with an injected subprocess transport to test full create/start/inspect/kill/remove lifecycle, exact security flags, argument boundaries, invalid paths, links/protected files, resource caps, output flood without newlines, timeout, partial start, cleanup failures, zero tests and concurrent unique names. No host execution fallback. Dependency: TASK-005. | No | — |

### Implementation Phase 3

- **GOAL-003**: Add Tester analysis and bounded graph routing. Depends on Phase 2. Completion: fake-provider integration demonstrates observed failure -> repair -> success through real graph execution.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | Add ntg_llm/tester.py with create_tester_agent(llm) -> Agent[TesterFeedback] and TesterAgent.assess(task, profile, report). For pass or infrastructure errors construct deterministic feedback without an LLM call. For code failure invoke the feedback Agent once with bounded JSON evidence; validate evidence references. On provider/schema failure retain safe error metadata and use deterministic report-based fallback; do not rerun the sandbox or infer success. Add tests/test_tester.py. Dependency: TASK-001; may develop alongside Phase 2, but integrate only after TASK-006. | No | — |
| TASK-008 | Add ntg_llm/project_workflow.py with run_verified_project and build_project_graph. State contains original task/context, fixed profile/policy, generation_count initially 0, reports/feedback, latest Coder result and terminal status. coder node calls existing tool-enabled Coder, incrementing generation_count once per completed invocation; sandbox node snapshots actual files and executes once; tester node assesses evidence. If passed end; if infrastructure/policy failure end blocked; if failed and generation_count < 1+max_repair_attempts route to coder; else end repair_exhausted. Set repairs_used=max(0,generation_count-1). Preserve partial run evidence on Coder/provider exceptions. Dependency: TASK-006 and TASK-007. | No | — |
| TASK-009 | Supply each repair Coder with original task/context, fixed profile and latest evidence/feedback, bounded as structured data. Require artifacts on disk, not just CodeOutput.code. Missing selected entrypoint after generation is a repairable artifact failure; invalid host profile is a preflight failure. Check workspace manifest still matches the tested snapshot before declaring success; mismatch ends blocked with stale_workspace. Include all attempt reports with their own snapshot IDs. Dependency: TASK-008. | No | — |
| TASK-010 | Add tests/test_project_workflow.py with real compiled graph and NTG client/fake provider. Cover initial success, failure/repair/pass with actual changed file bytes, exhaust limits 0/1/3, last allowed success, timeout feedback, no-test failure, infrastructure stop without repair, Tester fallback, provider failure after writes, unchanged trusted tests, snapshot mismatch and concurrent isolated histories. Assert evidence-dependent repair decisions, exact Coder/runner counts, existing inner tool limits and no action replay. Dependency: TASK-009. | No | — |

### Implementation Phase 4

- **GOAL-004**: Expose explicit CLI opt-in and prove real execution. Depends on Phase 3. Completion: legacy suite, new unit tests and required Docker integration pass; documentation identifies remaining limitations.

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | Extend main.py with --verify, --entrypoint, --test-suite, --sandbox-image, --max-repair-attempts (3), and --execution-timeout (30). Require --workspace and exactly one test profile when verifying; require immutable image ID and applicable overwrite opt-in. Reject verification-only flags outside verified mode. Validate/preflight before model calls. Print ProjectRunResult JSON; exit 0 passed, 1 exhausted/blocked, 2 argument errors. Preserve existing non-verify CLI output and direct APIs. Export new public factory/workflow/contracts lazily from ntg_llm/__init__.py. Add CLI tests to tests/test_main.py. Dependency: TASK-010. | No | — |
| TASK-012 | Add tests/test_sandbox_integration.py and an explicit pytest --require-sandbox option plus --sandbox-image-id option in tests/conftest.py. Optional local lane skips with reason if engine/image absent; required lane fails preflight. Use real containers with fake providers for an initially broken multi-file project and a fixed external test that passes only after actual repair. Also test infinite loop termination, spawned-child cleanup, output flood, disabled network, read-only source and resource denial. Existing live-provider HTTP block remains enabled. Dependency: TASK-011. | No | — |
| TASK-013 | Set NTG_TEST_IMAGE_ID to the immutable image ID returned by successful preflight. Run python -m pytest -q; in PowerShell run python -m pytest -q tests/test_sandbox_integration.py --require-sandbox --sandbox-image-id $env:NTG_TEST_IMAGE_ID; python -m ruff check .; python -m ruff format --check .; python main.py --help. Record actual results, image ID, engine/Python version and skip reasons. Required sandbox acceptance cannot pass while Docker is unavailable. Dependency: TASK-012 and DEP-002. | No | — |
| TASK-014 | Add docs/milestone9.md and update README.md with architecture, setup, test profiles, exact repair counting, resource/output bounds, results and provenance, privacy/partial effects and failure modes. Mark this plan Completed only after TASK-013 passes. Publish reviewed project files using the isolated Guide clone, preserving remote-only files and excluding credentials/state. Verify remote commit and workspace file hashes. Dependency: TASK-013. | No | — |

## 3. Alternatives

- **ALT-001**: Local subprocess with timeout. Rejected as sandbox: cwd/timeout do not isolate filesystem, environment, network or descendants from the host.
- **ALT-002**: Let an LLM Tester choose arbitrary shell commands and declare success. Rejected because evidence, permissions and retry control must remain host-owned.
- **ALT-003**: Remote managed sandbox. Viable later backend for SandboxRunner, but introduces service credentials, network availability and provider-specific costs not required by this milestone.
- **ALT-004**: Coder regenerates/changes its own tests until they pass. Rejected as the default acceptance oracle. Use fixed host-selected external tests; test-authoring is a separate feature.

## 4. Dependencies

- **DEP-001**: Existing langgraph==1.2.16, langchain-core==1.6.13, Pydantic, strict NTG validation, safe filesystem tools and provider router. No framework replacement or additional LLM provider SDK is needed.
- **DEP-002**: A running local Linux Docker engine and host-approved image with Python >=3.10 and stdlib unittest, referenced by actual immutable sha256 image ID. Currently unavailable: installed Docker CLI cannot connect to dockerDesktopLinuxEngine. Provision separately before the required real-sandbox test lane; do not invent a digest or claim images are present.
- **DEP-003**: Host-selected script or external unittest suite, separate writable project directory and explicit overwrite permission for repairs. Standard-library projects are the initial supported profile.

## 5. Files

- **FILE-001**: New ntg_llm/testing_models.py, sandbox.py and testing_harness.py contain execution contracts, isolation and test counting.
- **FILE-002**: New ntg_llm/tester.py and project_workflow.py contain the Tester role and outer repair graph.
- **FILE-003**: Existing ntg_llm/tools/filesystem.py gains checked snapshot support without loosening file policy; ntg_llm/coder.py only gains workflow-specific prompt guidance if required, retaining legacy no-execution claims.
- **FILE-004**: Existing main.py and ntg_llm/__init__.py expose opt-in verified workflow; existing service/client/strategy/store implementations remain unchanged.
- **FILE-005**: New tests/test_sandbox.py, test_tester.py, test_project_workflow.py and test_sandbox_integration.py; extend tests/conftest.py and test_main.py.
- **FILE-006**: README.md and new docs/milestone9.md document the completed behavior. This plan and docs/milestone9-feasibility.md record assessment and status.

## 6. Testing

- **TEST-001**: Preserve the entire Milestone 6 suite, including real LangGraph Coder behavior, strict file policy, routing retries, trace privacy and tool-free direct calls.
- **TEST-002**: Execution tests assert container controls, output/resource/time bounds, safe snapshot content and confirmed cleanup for every lifecycle branch.
- **TEST-003**: Repair tests assert one initial attempt plus at most R repairs, including R=0, success on the last allowed attempt and no further calls after exhaustion.
- **TEST-004**: Evidence-dependent real-container E2E demonstrates an actual runtime/test failure, actual feedback, actual changed files and actual passing re-execution against unchanged tests. Script-only exit zero and no-test discovery have distinct outcomes.
- **TEST-005**: Failure-path tests preserve earlier writes and provider diagnostics; infrastructure errors do not trigger code repairs; Tester errors do not suppress process evidence or create another execution.
- **TEST-006**: Validate no raw logs/credentials in routing diagnostics or tracing uploads, no tests writable by Coder, and no cross-run state/snapshot/container-name leakage.

## 7. Risks & Assumptions

- **RISK-001**: Docker is installed but unavailable now. Mocked subprocess tests alone cannot establish sandbox acceptance; the required real-container lane is a completion gate.
- **RISK-002**: Containers share the host/VM kernel and do not supply formal malicious-code isolation. Generated code may also tamper with in-process Python tests; this design targets accidental bugs, not adversarial grading.
- **RISK-003**: A process exit of zero proves only the selected check. The result must identify the verification kind; incomplete tests can miss semantic requirements.
- **RISK-004**: Source files and exception output may contain sensitive literals. Excluding known protected files, bounding output and avoiding trace uploads reduce exposure but do not guarantee generic secret recognition.
- **RISK-005**: Limits on repairs do not alone bound elapsed time. Retain finite provider request/retry limits and enforce execution/control deadlines; no unbounded poll/read or graph-node retries.
- **RISK-006**: A timeout may leave a running container if the Docker engine becomes unreachable. Record cleanup uncertainty and stop; never report success or silently continue repairs.
- **ASSUMPTION-001**: Python standard-library project, trusted host configuration, fixed external tests and no concurrent external workspace edits. Manifest mismatch ends blocked; stronger transactional workspaces are outside scope.
- **ASSUMPTION-002**: Initial default of three repairs is a policy choice, not a hardcoded milestone requirement. The validated host option can change it; model output cannot.
- **ASSUMPTION-003**: No milestones 7/8 implementation is assumed. Sandbox execution is explicitly included as a dependency of Milestone 9.

## 8. Related Specifications / Further Reading

- [Repository feasibility assessment](../docs/milestone9-feasibility.md)
- [Current LangGraph architecture](../docs/milestone6.md)
- [Existing filesystem policy](../docs/milestone5.md#workspace-policy)
- [Official LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Official Docker container execution and controls](https://docs.docker.com/engine/containers/run/)
