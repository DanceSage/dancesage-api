#!/bin/bash
# The platform server. Runs from the dance_sage conda env, which holds every dep.
cd "$(dirname "$0")"
export FIREBASE_PROJECT_ID=dancesage-61d8e

# R2 credentials, if they are here. Kept in a file rather than the shell so the
# server starts the same way however it is launched — and gitignored, so the
# secret cannot reach a commit.
if [ -f .env.r2 ]; then
  set -a; . ./.env.r2; set +a
  export STORAGE_BACKEND="${STORAGE_BACKEND:-r2}"
  # A laptop writing into the live bucket once overwrote a real recording,
  # because object names come from a video count and the two databases differ.
  # Anything not running on Fly gets its own corner.
  [ -z "$FLY_APP_NAME" ] && export R2_PREFIX="${R2_PREFIX:-local}"
fi

exec /opt/homebrew/Caskroom/miniconda/base/envs/dance_sage/bin/python \
  -m uvicorn dsplatform.main:app --host 0.0.0.0 --port 8000 "$@"
