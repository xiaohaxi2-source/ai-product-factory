#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
if command -v python3.12 >/dev/null 2>&1; then
  runtime_python="$(command -v python3.12)"
elif command -v python3 >/dev/null 2>&1 && python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 12))'; then
  runtime_python="$(command -v python3)"
else
  echo "请先安装Python 3.12，再运行安装入口。"
  exit 1
fi
product_dir="projects/AI产品工厂"
"$runtime_python" -m venv "$product_dir/backend/.venv"
"$product_dir/backend/.venv/bin/python" -m pip install -r "$product_dir/backend/requirements.txt"
echo "安装完成。请确认本机开发助手已登录，再双击start.command启动。"
