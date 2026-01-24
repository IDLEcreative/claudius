#!/bin/bash
# Git Operation Lock - Distributed mutex via flock
#
# Prevents concurrent git operations from Claudius (host) and Clode (Docker).
# Uses kernel-level flock which works across container boundaries since
# /opt/omniops is mounted into the Docker container.
#
# Usage:
#   git-lock.sh <repo-path> <command...>
#   git-lock.sh /opt/omniops git push origin main
#   git-lock.sh /opt/omniops git pull --rebase
#
# From Docker (Clode):
#   git-lock.sh /repo git commit -m "message"
#
# Exit codes:
#   0   - Command succeeded
#   124 - Lock acquisition timed out (another operation in progress)
#   *   - Command's own exit code

LOCK_TIMEOUT=120  # Max seconds to wait for lock
LOCK_SUFFIX=".git-operations.lock"

# Validate arguments
if [ $# -lt 2 ]; then
    echo "Usage: git-lock.sh <repo-path> <command...>" >&2
    exit 1
fi

REPO_PATH="$1"
shift
COMMAND=("$@")

# Lock file lives in the repo's .git directory (shared across host/container)
LOCK_FILE="${REPO_PATH}/.git/${LOCK_SUFFIX}"

if [ ! -d "${REPO_PATH}/.git" ]; then
    echo "Error: ${REPO_PATH} is not a git repository" >&2
    exit 1
fi

# Acquire lock with timeout, then execute command
exec 9>"${LOCK_FILE}"

if ! flock -w "${LOCK_TIMEOUT}" 9; then
    echo "Error: Could not acquire git lock after ${LOCK_TIMEOUT}s" >&2
    echo "Another git operation may be in progress." >&2
    echo "Lock file: ${LOCK_FILE}" >&2
    exit 124
fi

# Write lock metadata for debugging
echo "$$:$(hostname):$(date -Iseconds):${COMMAND[*]}" > "${LOCK_FILE}"

# Execute the command (lock is held via fd 9)
"${COMMAND[@]}"
EXIT_CODE=$?

# Lock is automatically released when fd 9 closes (script exit)
exec 9>&-

exit ${EXIT_CODE}
