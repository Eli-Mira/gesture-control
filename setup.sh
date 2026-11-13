#!/bin/bash
set -e
echo "=== Installing gesture-control ==="
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-gesture.txt
echo "Done. Run: python main.py"
