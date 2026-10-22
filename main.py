"""Generate structured Python source with one Coder Agent."""

import argparse

from ntg_llm import (
    LLMClient,
    LLMError,
    cli_entrypoint,
    create_coder_agent,
    create_filesystem_registry,
)


@cli_entrypoint
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", help="Describe the Python implementation you want")
    parser.add_argument("--context", help="Optional background information as text")
    parser.add_argument("--workspace", help="Enable file tools inside this existing directory")
    parser.add_argument(
        "--allow-overwrite",
        action="store_true",
        help="Allow explicit replacement requests within --workspace",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=8,
        help="LangChain single-action iteration ceiling, including final decision (default: 8)",
    )
    args = parser.parse_args()
    if args.allow_overwrite and args.workspace is None:
        parser.error("--allow-overwrite requires --workspace")
    if args.max_tool_calls < 1:
        raise LLMError(400, "max_tool_calls must be a positive integer")
    tools = (
        create_filesystem_registry(args.workspace, allow_overwrite=args.allow_overwrite)
        if args.workspace is not None
        else None
    )
    coder = create_coder_agent(LLMClient(), tools=tools, max_tool_calls=args.max_tool_calls)
    result = coder.run(args.task, context=args.context)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
