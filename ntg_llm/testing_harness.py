"""Trusted container harness; result stays in tmpfs until the host collects it."""

import json
import sys
import time
import unittest


def main():
    sys.path.insert(0, "/workspace")
    suite = unittest.defaultTestLoader.discover("/tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    payload = {
        "tests_run": result.testsRun,
        "exit_code": 0 if result.wasSuccessful() and result.testsRun else 1,
    }
    with open("/tmp/ntg-result.json", "w", encoding="utf-8") as handle:
        json.dump(payload, handle)
    # The host reads the bounded artifact while tmpfs is still mounted, then
    # removes this container. Never interpret project stdout as a test verdict.
    print(sys.argv[1], flush=True)
    time.sleep(int(sys.argv[2]))


if __name__ == "__main__":
    main()
