#!/bin/bash
# SessionStart hook for Claude Code on the web (cloud environments).
# Installs project dependencies so tests and linters work in remote sessions.
# Detects common manifests, so it keeps working as the project grows.
set -euo pipefail

# Only run in remote (cloud) sessions.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Node.js
if [ -f package.json ]; then
  if [ -f pnpm-lock.yaml ] && command -v pnpm >/dev/null; then
    pnpm install
  elif [ -f yarn.lock ] && command -v yarn >/dev/null; then
    yarn install
  else
    npm install
  fi
fi

# Python
if [ -f requirements.txt ]; then
  pip install -r requirements.txt
fi
if [ -f pyproject.toml ]; then
  pip install -e ".[dev]" 2>/dev/null || pip install -e .
fi

# Go
if [ -f go.mod ]; then
  go mod download
fi

# Rust
if [ -f Cargo.toml ]; then
  cargo fetch
fi

# Ruby
if [ -f Gemfile ]; then
  bundle install
fi

exit 0
