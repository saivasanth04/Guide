"""Opt-in live API + Docker repair demonstration: python -m scripts.live_milestone9."""

import argparse
import json
from pathlib import Path
import tempfile

from ntg_llm import LLMClient, LLMError
from ntg_llm.project_workflow import run_verified_project
from ntg_llm.sandbox import DockerSandboxRunner
from ntg_llm.service import CompletionService
from ntg_llm.settings import load_service
from ntg_llm.testing_models import ExecutionPolicy, TestProfile


TASK = """Implement label_tools.py with normalize_label(text).
Final requirements: strip leading/trailing whitespace, lowercase words, and join
whitespace-separated words with underscores; whitespace-only input returns "".
This is an explicit repair-workflow integration exercise with an intentionally
broken initial scaffold, not a normal implementation task.
On the FIRST generation (no execution_report in context), write exactly:
def normalize_label(text):
    return normalize_words(text)

Preserve that unresolved helper in the first generation so the host can verify
that runtime failure evidence reaches the Tester. Do not define it yet.
On SUBSEQUENT generations, when execution_report appears in context, use the
actual failure and Tester feedback to repair the module to meet the final
requirements. Write actual files via tools. Never modify the trusted test suite.
"""

TESTS = """import unittest
from label_tools import normalize_label

class Labels(unittest.TestCase):
    def test_words(self):
        self.assertEqual(normalize_label("  Hello   World  "), "hello_world")
    def test_empty(self):
        self.assertEqual(normalize_label(" \\t\\n "), "")
    def test_unicode(self):
        self.assertEqual(normalize_label("CAFÉ Menu"), "café_menu")
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sandbox-image", required=True)
    parser.add_argument("--output", default="artifacts/milestone9-live.json")
    parser.add_argument("--model", help="Optional model from existing local configuration")
    parser.add_argument(
        "--max-deployments",
        type=int,
        default=3,
        help="Bound live-test provider fallbacks per completion (default: 3)",
    )
    args = parser.parse_args()
    if args.max_deployments < 1:
        parser.error("max-deployments must be positive")
    policy = ExecutionPolicy(image_id=args.sandbox_image)
    with tempfile.TemporaryDirectory(prefix="ntg-live-m9-") as directory:
        base = Path(directory)
        workspace, tests = base / "project", base / "trusted-tests"
        workspace.mkdir()
        tests.mkdir()
        (tests / "test_labels.py").write_text(TESTS, encoding="utf-8")
        profile = TestProfile(kind="unittest", tests_root=str(tests))
        runner = DockerSandboxRunner(policy)
        runner.preflight(workspace, profile)
        configured = load_service()
        selected = [d for d in configured.deployments if not args.model or d.model == args.model]
        if not selected:
            parser.error("Requested model is absent from local configuration")
        service = CompletionService(
            selected, configured.ledger, max_attempts=args.max_deployments, retries_per_deployment=0
        )
        print("Running live Coder, real Docker tests, and live Tester feedback...", flush=True)
        result = run_verified_project(
            LLMClient(service=service),
            TASK,
            workspace=workspace,
            profile=profile,
            policy=policy,
            allow_overwrite=True,
            max_repair_attempts=3,
            max_tool_calls=8,
            runner=runner,
        )
        proof = {
            "test_kind": "live_api_and_real_docker_controlled_failure",
            "image_id": policy.image_id,
            "result": result.model_dump(mode="json"),
            "final_source": (workspace / "label_tools.py").read_text(encoding="utf-8"),
            "trusted_tests_unchanged": (tests / "test_labels.py").read_text(encoding="utf-8")
            == TESTS,
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(proof, indent=2, ensure_ascii=False), encoding="utf-8")
        summary = {
            "status": result.status,
            "repairs_used": result.repairs_used,
            "outcomes": [r.outcome for r in result.reports],
            "tests_run": result.reports[-1].tests_run,
            "coder_models": [d["model"] for d in result.diagnostics["coder"]],
            "tester_sources": [d["source"] for d in result.diagnostics["tester"]],
            "cleanup_confirmed": all(r.cleanup_confirmed for r in result.reports),
            "evidence": str(output.resolve()),
        }
        print(json.dumps(summary, indent=2), flush=True)
        if (
            result.status != "passed"
            or not result.repairs_used
            or result.reports[0].outcome != "failed"
            or result.diagnostics["tester"][0]["source"] != "model"
            or not proof["trusted_tests_unchanged"]
        ):
            raise SystemExit("Live repair acceptance failed; inspect saved evidence")


if __name__ == "__main__":
    try:
        main()
    except LLMError as error:
        print(json.dumps(error.as_dict(), indent=2))
        raise SystemExit(1) from None
