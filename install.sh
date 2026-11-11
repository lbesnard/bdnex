#!/bin/sh
# install.sh — Install the latest bdnex release from GitHub
# Usage: curl -fsSL https://raw.githubusercontent.com/lbesnard/bdnex/main/install.sh | sh

set -e

REPO="lbesnard/bdnex"
API_URL="https://api.github.com/repos/${REPO}/releases/latest"

echo "Fetching latest bdnex version..."
VERSION=$(curl -fsSL "$API_URL" | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p')

if [ -z "$VERSION" ]; then
  echo "Error: could not determine the latest version." >&2
  exit 1
fi

echo "Latest version: ${VERSION}"

WHEEL_URL="https://github.com/${REPO}/releases/download/v${VERSION}/bdnex-${VERSION}-py3-none-any.whl"

if command -v uv >/dev/null 2>&1; then
  echo "Installing via uv..."
  uv tool install "$WHEEL_URL" --force
elif command -v pipx >/dev/null 2>&1; then
  echo "Installing via pipx..."
  pipx install "$WHEEL_URL" --force
elif command -v pip >/dev/null 2>&1; then
  echo "Installing via pip..."
  pip install "$WHEEL_URL" --upgrade
else
  echo "Error: none of uv, pipx or pip was found. Install uv first: https://docs.astral.sh/uv/" >&2
  exit 1
fi

echo "bdnex ${VERSION} installed successfully."
