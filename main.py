"""Generate Python projects, optionally test them in Docker and repair failures."""

import argparse
import sys

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
    parser.add_argument(
        "task", nargs="?", help="Task description; prompts when omitted in a terminal"
    )
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
        help="LangGraph single-action decision ceiling, including final decision (default: 8)",
    )
    parser.add_argument("--verify", action="store_true", help="Execute checks and repair in Docker")
    parser.add_argument("--entrypoint", help="Relative Python script to execute")
    parser.add_argument("--test-suite", help="External trusted unittest directory")
    parser.add_argument("--sandbox-image", help="Approved local sha256 Docker image ID")
    parser.add_argument("--max-repair-attempts", type=int, default=None)
    parser.add_argument("--execution-timeout", type=int, default=None)
    args = parser.parse_args()
    if args.task is None:
        if not sys.stdin.isatty():
            parser.error('Provide a task, for example: python main.py "Write a square function"')
        print(
            "Describe the Python project or change you want.\nTask: ",
            end="",
            file=sys.stderr,
            flush=True,
        )
        try:
            args.task = input().strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            raise SystemExit(130) from None
    if not args.task.strip():
        parser.error("Task must not be empty")
    if args.allow_overwrite and args.workspace is None:
        parser.error("--allow-overwrite requires --workspace")
    if args.max_tool_calls < 1:
        raise LLMError(400, "max_tool_calls must be a positive integer")
    verification_options = (
        args.entrypoint,
        args.test_suite,
        args.sandbox_image,
        args.max_repair_attempts,
        args.execution_timeout,
    )
    if not args.verify and any(value is not None for value in verification_options):
        parser.error("Verification options require --verify")
    if args.verify:
        from pydantic import ValidationError
        from ntg_llm.testing_models import ExecutionPolicy, TestProfile
        from ntg_llm.sandbox import DockerSandboxRunner
        from ntg_llm.project_workflow import run_verified_project

        repairs = 3 if args.max_repair_attempts is None else args.max_repair_attempts
        if (
            not args.workspace
            or not args.sandbox_image
            or bool(args.entrypoint) == bool(args.test_suite)
        ):
            parser.error(
                "--verify requires workspace, sandbox-image and exactly one entrypoint/test-suite"
            )
        if not 0 <= repairs <= 10 or (repairs and not args.allow_overwrite):
            parser.error("Repairs must be 0..10 and require --allow-overwrite when nonzero")
        try:
            policy = ExecutionPolicy(
                image_id=args.sandbox_image,
                timeout_seconds=30 if args.execution_timeout is None else args.execution_timeout,
            )
            profile = TestProfile(
                kind="script" if args.entrypoint else "unittest",
                entrypoint=args.entrypoint,
                tests_root=args.test_suite,
            )
        except ValidationError:
            parser.error("Invalid verification policy or test profile")
        runner = DockerSandboxRunner(policy)
        runner.preflight(args.workspace, profile)
        result = run_verified_project(
            LLMClient(),
            args.task,
            context=args.context,
            workspace=args.workspace,
            policy=policy,
            profile=profile,
            max_repair_attempts=repairs,
            max_tool_calls=args.max_tool_calls,
            allow_overwrite=args.allow_overwrite,
            runner=runner,
        )
        print(result.model_dump_json(indent=2))
        if result.status != "passed":
            raise SystemExit(1)
        return
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
