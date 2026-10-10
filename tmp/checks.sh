#!/usr/bin/env bash
set -euo pipefail
echo "=== ruff ==="; ruff check src/pypackhub/core/backends/pip.py src/pypackhub/ui/views/settings.py
echo "=== black ==="; black --check src/pypackhub/core/backends/pip.py src/pypackhub/ui/views/settings.py
echo "=== mypy (affected files) ==="; mypy src/pypackhub/core/backends/pip.py src/pypackhub/ui/views/settings.py
echo "=== pytest relevant ==="; python -m pytest tests/ui/test_settings_view.py tests/test_backend_commands.py -q 2>&1 | tail -20
echo "ALL OK"
