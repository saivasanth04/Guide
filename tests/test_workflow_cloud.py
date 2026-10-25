"""Opt-in autonomous cloud decisions with real MCP/E2B and deliberate fault injection.

No model responses or execution results are mocked. The injected source defect
simulates a change after the Coder's self-check, exercising independent validation.
"""

import ast
import asyncio
import os
import shutil
from pathlib import Path

import pytest

from agent_orchestrator import run_workflow
from coder_agent import create_coder_agent, filesystem_config
from e2b_sandbox import E2BSandboxExecutor
from langchain_mcp_adapters.client import MultiServerMCPClient
from ntg_llm import LLMClient
from ntg_llm.langchain_adapter import NTGLangChainLLM
from tester_agent import TesterAgent as IndependentTester


pytestmark = pytest.mark.skipif(
    os.getenv("NTG_LIVE_CLOUD") != "1" or not shutil.which("npx"),
    reason="Set NTG_LIVE_CLOUD=1 with actual cloud/E2B credentials and npx",
)


def test_autonomous_cloud_repair_after_independent_failure(tmp_path):
    source = tmp_path / "converter.py"
    source.write_text('''import sys
from decimal import Decimal, InvalidOperation
def main():
    try:
        if len(sys.argv) != 2:
            raise ValueError("Provide one Celsius number")
        c = Decimal(sys.argv[1])
        if not c.is_finite():
            raise ValueError("Celsius must be finite")
        print(c * Decimal("1.8") + Decimal("32"))
    except (ValueError, InvalidOperation) as error:
        print("Error: " + str(error), file=sys.stderr)
        return 1
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
''', encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Temperature converter\nRun `python converter.py 0` to convert Celsius to Fahrenheit.\n",
        encoding="utf-8",
    )
    task = (
        "Complete the existing converter.py Celsius-to-Fahrenheit command-line utility. "
        "It accepts one Celsius number as its argument and prints Fahrenheit using F=C*9/5+32. "
        "0 Celsius must produce 32 Fahrenheit, 100 must produce 212, and -40 must produce -40. "
        "Reject invalid or non-finite input with a useful error and nonzero exit code. "
        "Keep README setup and usage instructions consistent with the actual invocation."
    )

    async def execute():
        tools = await MultiServerMCPClient(filesystem_config(str(tmp_path))).get_tools()
        sandbox = E2BSandboxExecutor(tmp_path)
        llm = NTGLangChainLLM(client=LLMClient(), reasoning_effort="low", trace_routing=True)
        real_coder = create_coder_agent(llm, [*tools, sandbox.as_tool()], tmp_path, verbose=False)
        real_tester = IndependentTester(llm, tools, tmp_path, verbose=True)
        phase = {"tester_started": False, "injections": 0}

        class ObserveTester:
            async def verify(self, requirement, summary):
                phase["tester_started"] = True
                return await real_tester.verify(requirement, summary)

        class InjectAfterSelfCheck:
            async def ainvoke(self, request):
                completed = await real_coder.ainvoke(request)
                if not phase["tester_started"]:
                    class CorruptOffset(ast.NodeTransformer):
                        changed = False

                        def visit_Constant(self, node):
                            if node.value in (32, 32.0, "32", "32.0", "32.00"):
                                self.changed = True
                                return ast.copy_location(ast.Constant("999" if isinstance(node.value, str) else 999), node)
                            return node

                    corrupt = CorruptOffset()
                    tree = corrupt.visit(ast.parse(source.read_text(encoding="utf-8")))
                    assert corrupt.changed, "Fault precondition: converter must contain its Fahrenheit offset"
                    source.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
                    phase["injections"] += 1
                    print("[Cloud validation] Deliberately injected incorrect conversion offset after self-check", flush=True)
                return completed

        try:
            result = await run_workflow(task, InjectAfterSelfCheck(), ObserveTester(), max_repair_attempts=3)
        finally:
            await asyncio.to_thread(sandbox.close)
        report = Path(__file__).resolve().parents[1] / "artifacts" / "cloud-repair-result.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        assert phase["injections"] > 0
        assert result.history[0].status == "FAIL", result
        assert result.history[0].failure_kind == "implementation", result
        assert result.history[0].evidence[0]["exit_code"] != 0, result
        assert result.status == "PASS", result
        assert 1 <= result.repair_attempts <= 3
        assert result.tester.evidence[-1]["exit_code"] == 0

    asyncio.run(execute())
