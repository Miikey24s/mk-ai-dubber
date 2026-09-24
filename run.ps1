$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    uv run vi-dubber @args
}
finally {
    Pop-Location
}
