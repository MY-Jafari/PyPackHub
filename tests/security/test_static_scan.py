"""SPEC section 11.9: the static scan must exist, run, and actually catch things.

A scanner that reports "clean" because it looks for nothing is worse than no
scanner, so half of these tests feed it code that *is* a violation and assert it
says so.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from pypackhub.core.runner import run

pytestmark = pytest.mark.security

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCANNER_PATH = REPOSITORY_ROOT / "scripts" / "security_scan.py"


def load_scanner() -> ModuleType:
    specification = importlib.util.spec_from_file_location("pypackhub_security_scan", SCANNER_PATH)

    assert specification is not None
    assert specification.loader is not None

    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)

    return module


scanner = load_scanner()

VIOLATIONS = [
    ("import os\nos.system('id')\n", "banned-call:os.system"),
    ("import os\nos.popen('id')\n", "banned-call:os.popen"),
    ("import subprocess\nsubprocess.run(['ls'], shell=True)\n", "banned-keyword:shell=True"),
    ("import subprocess\n", "spawn-outside-runner"),
    ("from subprocess import Popen\n", "spawn-outside-runner"),
    ("from os import system\nsystem('id')\n", "banned-call:os.system"),
    ("import pickle\npickle.loads(b'x')\n", "banned-call:pickle.loads"),
    ("import yaml\nyaml.load('x')\n", "banned-call:yaml.load"),
    ("eval('1 + 1')\n", "banned-call:eval"),
    ("exec('pass')\n", "banned-call:exec"),
    (
        "import httpx\nhttpx.get('https://example.org', verify=False)\n",
        "banned-keyword:verify=False",
    ),
    ("import tempfile\ntempfile.mktemp()\n", "banned-call:tempfile.mktemp"),
]


def write_target(tmp_path: Path, source: str) -> Path:
    target = tmp_path / "src"
    target.mkdir(parents=True, exist_ok=True)
    (target / "sample.py").write_text(source, encoding="utf-8")

    return target


def test_the_project_source_is_clean() -> None:
    findings = scanner.scan(REPOSITORY_ROOT)

    assert findings == [], "\n".join(finding.render(REPOSITORY_ROOT) for finding in findings)


@pytest.mark.parametrize("source,expected_rule", VIOLATIONS)
def test_a_violation_is_detected(tmp_path: Path, source: str, expected_rule: str) -> None:
    write_target(tmp_path, source)

    rules = {finding.rule for finding in scanner.scan(tmp_path)}

    assert expected_rule in rules, f"scanner missed {expected_rule} in {source!r}"


def test_the_runner_module_is_the_one_allowed_spawn_site(tmp_path: Path) -> None:
    target = tmp_path / "src" / "pypackhub" / "core"
    target.mkdir(parents=True)
    (target / "runner.py").write_text("import subprocess\n", encoding="utf-8")

    assert scanner.scan(tmp_path) == []


def test_a_banned_name_used_as_a_variable_is_not_a_finding(tmp_path: Path) -> None:
    write_target(tmp_path, "system = 'just a string'\nprint(system)\n")

    assert scanner.scan(tmp_path) == []


def test_the_scanner_reports_where_the_problem_is(tmp_path: Path) -> None:
    write_target(tmp_path, "import os\n\n\nos.system('id')\n")

    finding = scanner.scan(tmp_path)[0]

    assert finding.line == 4
    assert "sample.py" in finding.render(tmp_path)


def test_the_command_line_entry_point_reports_success() -> None:
    result = run(
        [sys.executable, str(SCANNER_PATH), "--root", str(REPOSITORY_ROOT)],
        timeout=120,
    )

    assert result.returncode == 0, result.output
    assert "clean" in result.stdout


def test_the_command_line_entry_point_fails_on_a_violation(tmp_path: Path) -> None:
    write_target(tmp_path, "import os\nos.system('id')\n")

    result = run(
        [sys.executable, str(SCANNER_PATH), "--root", str(tmp_path)],
        timeout=120,
    )

    assert result.returncode == 1
    assert "os.system" in result.stderr
    assert "findings" in result.stderr
