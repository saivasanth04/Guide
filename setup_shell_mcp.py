"""Install tumf/mcp-shell-server 1.1.14 with narrow Windows startup fixes."""

import os
from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".venv-shell"
PYTHON = ENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def main():
    uv = shutil.which("uv")
    if not uv:
        raise SystemExit("uv is required to install the isolated shell server.")
    if not PYTHON.is_file():
        subprocess.run([uv, "venv", "--python", sys.executable, str(ENV)], check=True)
    subprocess.run(
        [uv, "pip", "install", "--python", str(PYTHON),
         "--reinstall-package", "mcp-shell-server", "--refresh-package", "mcp-shell-server",
         "--link-mode", "copy", "--no-cache",
         "mcp-shell-server==1.1.14", "pytest==9.2.0"],
        check=True,
    )
    if os.name == "nt":
        package = ENV / "Lib/site-packages/mcp_shell_server"
        executor = package / "shell_executor.py"
        source = executor.read_text(encoding="utf-8")
        source = source.replace(
            "\nimport pwd\n",
            "\ntry:\n    import pwd\nexcept ImportError:\n    pwd = None\n",
            1,
        )
        source = source.replace(
            "    def _get_default_shell(self) -> str:\n",
            "    def _get_default_shell(self) -> str:\n"
            "        if os.name == 'nt':\n"
            "            return os.environ.get('COMSPEC', 'cmd.exe')\n",
            1,
        )
        executor.write_text(source, encoding="utf-8")
        server = package / "server.py"
        source = server.read_text(encoding="utf-8")
        source = source.replace(
            "\n        loop.add_signal_handler(sig, handle_signal)\n",
            "\n        if os.name != 'nt':\n"
            "            loop.add_signal_handler(sig, handle_signal)\n",
        ).replace(
            "\n            loop.remove_signal_handler(sig)\n",
            "\n            if os.name != 'nt':\n"
            "                loop.remove_signal_handler(sig)\n",
        )
        server.write_text(source, encoding="utf-8")
    print("Shell MCP installed. Run python verify_shell_mcp.py to verify.")


if __name__ == "__main__":
    main()
