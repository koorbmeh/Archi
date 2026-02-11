# Run from repo root in a terminal where git is on PATH (e.g. Git Bash or Cursor terminal).
# Usage: .\scripts\commit_and_push.ps1

Set-Location $PSScriptRoot\..

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Error "git not found. Run this in Git Bash or a terminal where git is on PATH."
    exit 1
}

if (-not (Test-Path .git)) {
    git init
    git branch -M main
}

git add .
git status

$confirm = Read-Host "Proceed with commit and push? (y/n)"
if ($confirm -ne "y") { exit 0 }

git commit -m "Initial commit: Archi agent (Gate A + Gate B local LLM)"
$remote = git remote get-url origin 2>$null
if (-not $remote) {
    git remote add origin https://github.com/koorbmeh/Archi.git
}
git push -u origin main

Write-Host "Done."
