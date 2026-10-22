from ntg_llm import LLMClient, cli_entrypoint
from pydantic import BaseModel
import os

class Response(BaseModel):
    response: str
    reasoning: str
class Agent:

    def __init__(self, name, system_prompt, llm):
        self.name = name
        self.system_prompt = system_prompt
        self.llm = llm

    def run(self, task, context=None):

        prompt = f"""
Task:
{task}

Context:
{context if context else "No additional context."}
"""

        response = self.llm.models.generate_content(
            model="router",
            contents=prompt,
            config={
                "system_instruction": self.system_prompt,
                "response_mime_type": "application/json",
                "response_schema": Response,
            },
        )

        return response.parsed


client = LLMClient()
coder = Agent(
    name="coder",
    system_prompt="""
You are a Python software engineer.

Your job is to:
- Understand the requested programming task.
- Write correct and clean Python code.
- Briefly summarize your solution.
- Explain important implementation decisions.
""",
    llm=client,
)
result = coder.run(
    task="Write a Python function that returns the factorial of a number."
)
print(result.response)



