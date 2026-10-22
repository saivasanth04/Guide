# From the repository root: .\examples\milestone9\run.ps1
# Prerequisite: running Docker Desktop Linux engine and a local python:3.13-slim image.
$ErrorActionPreference = 'Stop'
$ntgImage = docker image inspect python:3.13-slim --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'Prepare the sandbox image first: docker pull python:3.13-slim' }
$ntgWorkspace = Join-Path $PSScriptRoot 'workspace'
New-Item -ItemType Directory -Force -Path $ntgWorkspace | Out-Null
$ntgTests = Join-Path $PSScriptRoot 'trusted_tests'
$ntgMain = Join-Path $PSScriptRoot '..\..\main.py'
python $ntgMain 'Create label_tools.py with normalize_label(text): strip whitespace, lowercase words and join them with underscores. Whitespace-only input returns an empty string.' --verify --workspace $ntgWorkspace --test-suite $ntgTests --sandbox-image $ntgImage --allow-overwrite --max-repair-attempts 3
exit $LASTEXITCODE
