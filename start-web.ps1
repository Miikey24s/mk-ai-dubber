$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    uv run vi-dubber web @args
}
finally {
    Pop-Location
}
