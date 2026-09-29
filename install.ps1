# Installs the deepscrape skill for Claude Code (Windows).
# Usage: .\install.ps1            -> user-level (~\.claude\skills), available in every project
#        .\install.ps1 -Project   -> current project only (.\.claude\skills)
param([switch]$Project)
$dest = if ($Project) { Join-Path (Get-Location) ".claude\skills" } else { Join-Path $HOME ".claude\skills" }
New-Item -ItemType Directory -Force $dest | Out-Null
Copy-Item -Recurse -Force (Join-Path $PSScriptRoot "skills\deepscrape") $dest
python -m pip install -r (Join-Path $PSScriptRoot "requirements.txt")
scrapling install
Write-Host "Installed to $dest\deepscrape. Restart Claude Code, then ask it to scrape a website."
