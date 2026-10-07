#!/usr/bin/env bash
#
# Regenerate the dependency locks that are committed to the repository.
#
# Run this after changing [project.dependencies] or the "dev" extra in
# pyproject.toml, then commit the two regenerated files. The CI workflow
# re-runs this script and fails when the result differs from what is committed,
# so the locks can never silently drift away from pyproject.toml.
#
# The resolution is universal (`--universal`) and targets the lowest supported
# Python (3.10), which produces one lock file that is valid on Windows, macOS
# and Linux across every supported interpreter version.
#
# `--generate-hashes` records the SHA256 of every candidate artifact, which is
# what makes `pip install --require-hashes -r requirements.lock` possible now
# rather than in Phase 4, and lets `pip-audit --disable-pip` audit exactly the
# pinned set without re-resolving anything.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "error: uv is required to regenerate the locks (https://docs.astral.sh/uv/)" >&2
  exit 1
fi

lock_args=(--universal --python-version 3.10 --no-header --generate-hashes)

uv pip compile pyproject.toml "${lock_args[@]}" -o requirements.lock
uv pip compile pyproject.toml --extra dev "${lock_args[@]}" -o requirements-dev.lock

echo "Regenerated requirements.lock and requirements-dev.lock."
