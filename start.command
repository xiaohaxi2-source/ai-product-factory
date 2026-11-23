#!/bin/zsh
cd "${0:A:h}"
exec "projects/AI产品工厂/backend/.venv/bin/python" "projects/AI产品工厂/scripts/server.py"
