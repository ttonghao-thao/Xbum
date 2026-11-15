#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
umask 077
if [[ "$(uname -s)" != Darwin ]]; then
  echo '此脚本针对 macOS；其他平台请手工安装 Python 3.11+ / Node 22.12+ / FFmpeg。' >&2
  exit 1
fi
echo "机器架构: $(uname -m) · 统一内存: $(sysctl -n hw.memsize) bytes"
for dependency in python3.12:python@3.12 node:node@22 ffmpeg:ffmpeg ollama:ollama whisper-cli:whisper-cpp uv:uv; do
  binary="${dependency%%:*}"
  formula="${dependency#*:}"
  if ! command -v "$binary" >/dev/null; then
    command -v brew >/dev/null || { echo '缺少依赖。请先从 https://brew.sh 安装 Homebrew。' >&2; exit 1; }
    HOMEBREW_NO_INSTALL_CLEANUP=1 brew install "$formula"
    if [[ "$formula" == node@22 ]]; then export PATH="$(brew --prefix node@22)/bin:$PATH"; fi
    if [[ "$formula" == python@3.12 ]]; then export PATH="$(brew --prefix python@3.12)/bin:$PATH"; fi
  fi
done
node -e 'const [major,minor]=process.versions.node.split(".").map(Number); if(major<22||(major===22&&minor<12)){console.error("需要 Node 22.12+，请更新 Node");process.exit(1)}'
uv sync --all-extras --frozen --python "$(command -v python3.12)"
if [[ ! -f .env ]]; then cp .env.example .env; fi
mkdir -p data models backups runtime
chmod 700 data models backups runtime
npm ci --prefix apps/web
.venv/bin/alembic upgrade head
npm run build --prefix apps/web
echo '安装完成。make start 启动；浏览器 /setup 设置口令。模型单独运行 make models 下载。'
