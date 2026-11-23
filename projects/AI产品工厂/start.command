#!/bin/zsh
cd "${0:A:h}"
exec backend/.venv/bin/python scripts/server.py
