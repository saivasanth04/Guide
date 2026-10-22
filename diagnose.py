"""Read saved access diagnostics, or explicitly recheck the blocked deployment."""

import argparse
import json
from chat_client import _get_service
from llm import cli_entrypoint


@cli_entrypoint
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--probe-blocked",
        action="store_true",
        help="Make ONE live hi request using only the blocked key/model. No retries or fallback. "
        "Clear the same saved stop only if this request succeeds.",
    )
    options = parser.parse_args()
    service = _get_service()
    result = (
        service.probe_blocked()
        if options.probe_blocked
        else {"blocked": service.blocked_diagnostics(), "live_call_made": False}
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
