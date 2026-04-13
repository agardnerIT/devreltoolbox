#!/usr/bin/env bash
set -euo pipefail

# Purpose:
# Some host-mounted workspaces come in with UID/GID ownership that does not match
# the container user (usually `vscode`). When that happens, normal writes fail.
#
# This script runs on devcontainer start (postStartCommand) and does a quick
# writability check. If write access is broken, it applies a best-effort `chown`
# fix and re-checks. If it still fails, it prints next-step diagnostics.

TARGET_DIR="${1:-/workspaces/devreltoolbox}"

# If the target path is not mounted/present, do nothing and exit cleanly.
if [[ ! -d "$TARGET_DIR" ]]; then
  echo "[devcontainer-perms] Skip: target does not exist: $TARGET_DIR"
  exit 0
fi

# We use a temporary probe file to test real write capability as the current user.
probe_file="$TARGET_DIR/.perm_probe"

# Fast path: workspace is already writable; no action needed.
if touch "$probe_file" 2>/dev/null; then
  rm -f "$probe_file"
  echo "[devcontainer-perms] Workspace already writable: $TARGET_DIR"
  exit 0
fi

echo "[devcontainer-perms] Workspace not writable, attempting ownership fix on $TARGET_DIR"

# Best-effort: remap workspace ownership to the active remote user.
# This may emit errors on some mounted git object files; we ignore and re-check writability.
sudo chown -R "$(id -u):$(id -g)" "$TARGET_DIR" >/dev/null 2>&1 || true

# Verify whether the attempted fix actually restored write access.
if touch "$probe_file" 2>/dev/null; then
  rm -f "$probe_file"
  echo "[devcontainer-perms] Workspace write fix succeeded"
  exit 0
fi

# If still not writable, keep startup non-blocking and print actionable guidance.
echo "[devcontainer-perms] WARNING: workspace is still not writable."
echo "[devcontainer-perms] Run: /workspaces/devreltoolbox/blah/uid_debug_report.sh"
echo "[devcontainer-perms] Then run: /workspaces/devreltoolbox/blah/uid_fix_fast.sh"
exit 0
