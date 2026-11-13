@echo off
echo === Installing gesture-control ===
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-gesture.txt
echo Done. Run: python main.py
