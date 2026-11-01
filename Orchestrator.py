from typing import TypedDict

from langgraph.graph import StateGraph, START, END

from Planner_agent import planner_agent
from Researcher_agent import researcher_agent
from Coder_agent import coder_agent
from Tester_agent import tester_agent
from Reviewer_agent import reviewer_agent


MAX_REPAIR_ATTEMPTS = 3


class AgentState(TypedDict):
    task: str
    plan: str
    research: str
    coder_result: str
    test_result: str
    review_result: str
    feedback: str
    repair_attempts: int


async def planner_node(state: AgentState):
    result = await planner_agent(state["task"])

    return {
        "plan": result,
    }


async def researcher_node(state: AgentState):
    result = await researcher_agent(
        f"""
TASK:
{state["task"]}

PLAN:
{state["plan"]}

Research only information relevant to implementing this task.
"""
    )

    return {
        "research": result,
    }


async def coder_node(state: AgentState):
    feedback = state.get("feedback", "")

    prompt = f"""
TASK:
{state["task"]}

PLAN:
{state["plan"]}

RESEARCH:
{state["research"]}
"""

    if feedback:
        prompt += f"""

FAILURE/REVIEW FEEDBACK:
{feedback}

Inspect the current implementation and fix the root cause.
Do not rewrite working code unnecessarily.
"""

    result = await coder_agent(prompt)

    return {
        "coder_result": result,
        "feedback": "",
    }


async def tester_node(state: AgentState):
    result = await tester_agent(
        f"""
Validate the implementation currently present in the workspace.

ORIGINAL TASK:
{state["task"]}

Return:

RESULT: PASS

or

RESULT: FAIL

Include concrete execution evidence.
"""
    )

    return {
        "test_result": result,
    }


async def reviewer_node(state: AgentState):
    result = await reviewer_agent(state["task"])

    return {
        "review_result": result,
    }


def after_test(state: AgentState):
    result = state["test_result"].upper()

    if "RESULT: PASS" in result:
        return "reviewer"

    if state["repair_attempts"] >= MAX_REPAIR_ATTEMPTS:
        return END

    return "test_repair"


async def test_repair_node(state: AgentState):
    return {
        "feedback": state["test_result"],
        "repair_attempts": state["repair_attempts"] + 1,
    }


def after_review(state: AgentState):
    result = state["review_result"].upper()

    if "VERDICT: APPROVE" in result and "VERDICT: REJECT" not in result:
        return END

    if state["repair_attempts"] >= MAX_REPAIR_ATTEMPTS:
        return END

    return "review_repair"


async def review_repair_node(state: AgentState):
    return {
        "feedback": state["review_result"],
        "repair_attempts": state["repair_attempts"] + 1,
    }


workflow = StateGraph(AgentState)

workflow.add_node("planner", planner_node)
workflow.add_node("researcher", researcher_node)
workflow.add_node("coder", coder_node)
workflow.add_node("tester", tester_node)
workflow.add_node("test_repair", test_repair_node)
workflow.add_node("reviewer", reviewer_node)
workflow.add_node("review_repair", review_repair_node)


workflow.add_edge(START, "planner")

workflow.add_edge("planner", "researcher")
workflow.add_edge("researcher", "coder")
workflow.add_edge("coder", "tester")

workflow.add_conditional_edges(
    "tester",
    after_test,
    {
        "reviewer": "reviewer",
        "test_repair": "test_repair",
        END: END,
    },
)

workflow.add_edge("test_repair", "coder")

workflow.add_conditional_edges(
    "reviewer",
    after_review,
    {
        "review_repair": "review_repair",
        END: END,
    },
)

workflow.add_edge("review_repair", "coder")


graph = workflow.compile()


async def run_task(task: str):
    return await graph.ainvoke(
        {
            "task": task,
            "plan": "",
            "research": "",
            "coder_result": "",
            "test_result": "",
            "review_result": "",
            "feedback": "",
            "repair_attempts": 0,
        }
    )