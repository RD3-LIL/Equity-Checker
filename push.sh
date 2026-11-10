#!/bin/bash
# One-command push for the equity checker.
# Usage: ./push.sh "what you changed"
# If you leave the message out, it uses "update" with today's date.
# This rebuilds index.html first, so GitHub Pages always matches your DB.
set -e
cd "$(dirname "$0")"
python3 generate_report.py
git add .
git commit -m "${1:-update $(date +%Y-%m-%d)}"
git push
echo "Pushed. Pages will rebuild in 1-2 minutes."
