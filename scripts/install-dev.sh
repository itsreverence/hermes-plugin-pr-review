#!/usr/bin/env bash
set -euo pipefail

PLUGIN_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/plugins/pr_review"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
PLUGIN_DEST="$HERMES_HOME/plugins/pr-review"

mkdir -p "$HERMES_HOME/plugins"

if [ -e "$PLUGIN_DEST" ] || [ -L "$PLUGIN_DEST" ]; then
  existing="$(readlink -f "$PLUGIN_DEST" 2>/dev/null || true)"
  if [ "$existing" != "$PLUGIN_SRC" ]; then
    echo "Replacing existing pr-review plugin path: ${existing:-$PLUGIN_DEST}"
    rm -rf "$PLUGIN_DEST"
  else
    echo "pr-review plugin symlink already installed: $PLUGIN_DEST -> $PLUGIN_SRC"
  fi
fi

if [ ! -e "$PLUGIN_DEST" ] && [ ! -L "$PLUGIN_DEST" ]; then
  ln -s "$PLUGIN_SRC" "$PLUGIN_DEST"
  echo "Installed pr-review plugin symlink: $PLUGIN_DEST -> $PLUGIN_SRC"
fi

hermes plugins enable pr-review
hermes plugins list | grep -A2 -B2 'pr-review' || true
hermes pr-review setup
