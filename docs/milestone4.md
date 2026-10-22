# Milestone 4 — One reusable Agent and one Coder

This document describes the tool-free mode. See [Milestone 5](milestone5.md) for
the optional workspace tools and decision loop.

An Agent combines a name, role instructions, an injected LLM client, and an output
model. Each `run` adds a task and optional context, invokes the client once, and
returns typed output with routing diagnostics. The generic class works with any
supported response model; coding is the first configuration supplied by this repo.

## Run the Coder

```powershell
python -m pip install -e ".[dev]"
python main.py "Write a Python function that checks whether a number is prime" --context "Use the standard library only."
```

The task argument is required. `python main.py --help` shows usage without loading
credentials. Running without a task exits with status 2. `--context` supplies text;
it does not read files. Normal runs use the existing local provider configuration.

Successful output has this structure; the model and diagnostics reflect the actual
selected deployment:

```json
{
  "agent_name": "coder",
  "output": {
    "code": "def square(value):\n    return value * value",
    "explanation": "Returns the square of the input."
  },
  "model": "selected-model",
  "diagnostics": {
    "attempt_count": 1,
    "failed_attempts": []
  }
}
```

The Coder returns Python source and a brief explanation. Both fields must be
nonblank strings. `ast.parse` checks the source without executing it or writing it
to a file. Parsing does not prove semantic correctness or guarantee that a program
will run successfully. Syntax or output-schema failures produce a sanitized 502;
there is no automatic output-repair call.

## Use the Python interface

```python
from ntg_llm import LLMClient, create_coder_agent

coder = create_coder_agent(LLMClient())
result = coder.run(
    "Implement the requested string transformation",
    context="Convert spaces to underscores and preserve other characters.",
)
print(result.output.code)
print(result.model_dump_json(indent=2))
```

The role is configuration. To use the generic interface with your own instructions
and output contract:

```python
from pydantic import BaseModel

from ntg_llm import Agent, LLMClient


class Summary(BaseModel):
    summary: str


agent = Agent(
    name="document-helper",
    system_prompt="Summarize the supplied document using the requested format.",
    llm=LLMClient(),
    response_model=Summary,
)
result = agent.run("Summarize this text", context="Your document text")
print(result.output.summary)
```

This illustrates configuration of the same class; the repository provides only
one role factory, `create_coder_agent`. It does not create a team of agents.

## Contract and isolation

- Constructor: `Agent(name, system_prompt, llm, *, response_model=Answer)`.
- Run: `run(task: str, context: str | None = None) -> AgentResult`.
- `AgentResult` contains `agent_name`, typed `output`, selected `model`, and safe
  `diagnostics`. Pydantic provides JSON serialization for the complete result.
- Name, instructions, and task must be nonempty strings. Context must be text or
  `None`; an empty context string is allowed. Invalid input/schema returns 400
  before the client is called.
- Agent configuration is frozen. Each run creates fresh messages: configured
  instructions as the system message and JSON-encoded task/context as user data.
  No history or context is retained between calls. Caller-owned clients still
  need to support the concurrency level at which they are used.
- Structured output comes from `message.parsed`, preserving Milestone 3's schema
  validation. Custom validators should remain pure and idempotent because the
  current client can validate response objects more than once.

## Routing, errors, and scope

One Agent run makes one client invocation. The existing router can retry a
transient failure or move to the next eligible account/model within that call.
The Agent adds no retry loop. Results retain failed attempts, key labels, project
IDs, selected models, and provider retry information when available.

An `LLMError` is re-raised with copied diagnostics plus `agent_name`; its status,
message, retry timing, and retry/fallback flags are preserved. The original error
is not modified. CLI provider/validation errors print sanitized JSON to stderr
and exit with status 1. Task/context are not automatically added to error metadata.

The current Agent has an empty tool set: it does not execute code, run shell
commands, write generated files, discover repositories, maintain persistent
memory, or communicate with other agents. Those capabilities need their own
interfaces and lifecycle. A caller can pass relevant text explicitly as context.

## Verification

The offline suite covers role/context construction, arbitrary output models,
repeated and concurrent runs, preserved error diagnostics, Coder validation,
non-execution of generated source, inherited router recovery, and the CLI.
Tests use fake providers and temporary SQLite databases. Existing persistence,
provider, structured-output, and compatibility tests are included.

Verified on 2026-10-22: **109 mocked tests passed**, and Ruff lint and formatting
checks passed. No live API calls were made.

Run `python -m pytest -q`, `python -m ruff check .`, and
`python -m ruff format --check .`. No live API calls are required for these checks.

See the [implementation plan](../plan/feature-agent-milestone4-v1.md) and
[feasibility assessment](milestone4-feasibility.md) for the design decisions.
