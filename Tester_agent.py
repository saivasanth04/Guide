import asyncio
from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from mcp_registry import get_mcp_tools


WORKSPACE_DIR = r"C:\Users\perur\Desktop\workspace"
TESTS_DIR = r"C:\Users\perur\Desktop\workspace\tests"

ROLE = """
You are an autonomous QA Automation Engineer and Test Architect. You review source code, construct organized pytest suites in a dedicated tests folder, and execute them locally using the terminal tool.
"""

INSTRUCTIONS = f"""
1. DISCOVERY & PREPARATION:
   - Use the filesystem tools to inspect the source file in the workspace (e.g., `{WORKSPACE_DIR}\\factorial.py`).
   - Ensure the tests directory exists: `{TESTS_DIR}`.
   - Ensure `{TESTS_DIR}\\__init__.py` exists (create an empty one if missing to maintain package structure).

2. TEST SUITE AUTHORING:
   - Author a pytest file named `test_factorial.py` and write it directly into: `{TESTS_DIR}\\test_factorial.py`.
   - Import the target function cleanly: `from factorial import calculate_factorial`.
   - Cover all critical paths:
     * Base cases (0 and 1).
     * Standard positive values (e.g., 5 -> 120).
     * Exception handling with `pytest.raises(ValueError)` for negatives.
     * Exception handling with `pytest.raises(TypeError)` for floats, strings, and None.

3. EXECUTION & VALIDATION:
   - Use Desktop Commander start_process (PowerShell); read_process_output if still running.
   - Run pytest with the exact command:
     Set-Location -LiteralPath "{WORKSPACE_DIR}"; python -m pytest tests/ -v
   - If tests fail, inspect the stdout/stderr traceback, fix any assertion or syntax errors in `{TESTS_DIR}\\test_factorial.py`, and re-run.

4. REPORT:
   - Output the captured pytest execution logs, pass/fail counts, and a final QA sign-off.
"""

CONTEXT = f"""
- Workspace root: {WORKSPACE_DIR}
- Test directory: {TESTS_DIR}
- Execution: Always run `python -m pytest tests/ -v` from the workspace root so that Python automatically resolves `{WORKSPACE_DIR}` on `sys.path`.
- No external paid APIs are used; all tests execute locally.
"""


async def tester_agent(user_request: str):
    adapter, tools = await get_mcp_tools()

    try:
        model = ChatOpenAI(
            model="gemini/gemini-3.1-flash-lite",
            base_url="http://localhost:8080/v1",
            api_key="dummy",
        )

        agent = create_agent(
            model=model,
            tools=tools,
            system_prompt=f"""
ROLE:
{ROLE}

INSTRUCTIONS:
{INSTRUCTIONS}

CONTEXT:
{CONTEXT}
""",
        )

        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": user_request}]}
        )

        return result["messages"][-1].content

    finally:
        await adapter.__aexit__(None, None, None)


async def main():
    result = await tester_agent(
        f"""
1. Inspect factorial.py in the workspace root.
2. Create tests/__init__.py and write tests/test_factorial.py covering happy path, 0, 1, negatives, and invalid types.
3. Run the test suite using: Set-Location -LiteralPath "{WORKSPACE_DIR}"; python -m pytest tests/ -v
4. Print the raw execution output and summary.
"""
    )
    print(result)


if __name__ == "__main__":
    asyncio.run(main())
