#!/usr/bin/env sh
# Installs the deepscrape skill for Claude Code (macOS/Linux).
# Usage: ./install.sh            -> user-level (~/.claude/skills)
#        ./install.sh --project  -> current project only (./.claude/skills)
set -e
here="$(cd "$(dirname "$0")" && pwd)"
if [ "$1" = "--project" ]; then dest="$PWD/.claude/skills"; else dest="$HOME/.claude/skills"; fi
mkdir -p "$dest"
cp -r "$here/skills/deepscrape" "$dest/"
python3 -m pip install -r "$here/requirements.txt"
scrapling install
echo "Installed to $dest/deepscrape. Restart Claude Code, then ask it to scrape a website."
