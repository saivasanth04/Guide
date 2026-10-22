"""Send one prompt and print one chat completion."""

import argparse
import sys

from ntg_llm import LLMClient, cli_entrypoint


@cli_entrypoint
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", help="Message to send; prompts when omitted")
    args = parser.parse_args()
    prompt = args.prompt
    if prompt is None:
        try:
            print("You: ", end="", file=sys.stderr, flush=True)
            prompt = input()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            raise SystemExit(130) from None
    if not prompt.strip():
        parser.error("Prompt must not be empty")
    response = LLMClient().chat.completions.create(
        messages=[{"role": "user", "content": prompt.strip()}]
    )
    print(response.choices[0].message.content)


if __name__ == "__main__":
    main()
