# Local Windows shell MCP

Pinned to @mako10k/mcp-shell-server 2.8.1. config.yaml launches this copy using Node and explicitly loads the repository .env.

The patch uses os.tmpdir() for internal files and output, launches non-interactive commands through PowerShell on Windows, and defaults interactive terminals to PowerShell. Workspace variables and timeout stay in .env. MCP_SHELL_ENHANCED_MODE=false and MCP_SHELL_LLM_EVALUATION=false disable the optional sampling evaluator for the configured permissive execution mode.

To rebuild after deleting/reinstalling node_modules, run from the repository directory:

```powershell
npm ci --prefix .shell-mcp
git apply --directory=.shell-mcp .shell-mcp/windows.patch
```

Apply the patch once to a fresh installation. Restart GoModel after config or .env changes. Current validation covers initialization, foreground PowerShell and Python commands, and nonzero exit-code reporting; interactive terminal and detached execution have not been tested.
