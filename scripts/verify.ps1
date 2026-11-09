param(
    [ValidateRange(1, 20)]
    [int]$Iterations = 3
)

$ErrorActionPreference = 'Stop'
$projectDirectory = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectDirectory
try {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Parser/API/state tests failed.' }
    python -m tests.browser_suite --iterations $Iterations
    if ($LASTEXITCODE -ne 0) { throw 'Playwright browser tests failed. See .local/browser-tests.' }
    Write-Output 'Verification passed. Browser traces and screenshots: .local/browser-tests/'
} finally {
    Pop-Location
}
