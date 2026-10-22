#!/usr/bin/env python3
"""Fail the build when a banned pattern appears in the project's own source.

This is the project's own static check, the companion to bandit. It exists
because the rules in SPEC section 11 are specific to this project and bandit
does not know about them - most importantly that :mod:`subprocess` may be
imported from exactly one module.

The check is AST based rather than a text search, so it cannot be fooled by
formatting, and cannot be silenced by naming a variable ``eval``. It also
resolves ``from os import system`` style imports back to their origin, because
that is the obvious way to slip past a naive scanner.

Usage::

    python scripts/security_scan.py              # scan the repository
    python scripts/security_scan.py --root DIR   # scan another tree (used by tests)

Exit code 0 means clean, 1 means at least one finding.
"""

from __future__ import annotations

import argparse
import ast
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[1]

#: Directories scanned by default, relative to the root.
DEFAULT_TARGETS: Final = ("src", "scripts")

#: The only modules allowed to import `subprocess`. Everything else has to
#: go through `pypackhub.core.runner`; the elevated helper is the second
#: half of that same spawn site - the stateless process the runner starts
#: elevated on Windows, which runs the one confirmed command the runner
#: hands it, under the runner's rules (argument list only, no shell).
SUBPROCESS_ALLOWED_FILES: Final = (
    Path("src/pypackhub/core/runner.py"),
    Path("src/pypackhub/core/elevated_helper.py"),
)

#: Dotted call targets that may never appear in the project's source.
BANNED_CALLS: Final = {
    "os.system": "use pypackhub.core.runner instead of handing a string to a shell",
    "os.popen": "use pypackhub.core.runner instead of opening a pipe to a shell",
    "os.execv": "replacing the running process is never intended here",
    "os.execve": "replacing the running process is never intended here",
    "os.execl": "replacing the running process is never intended here",
    "os.execle": "replacing the running process is never intended here",
    "os.execvp": "replacing the running process is never intended here",
    "os.execvpe": "replacing the running process is never intended here",
    "os.execlp": "replacing the running process is never intended here",
    "os.execlpe": "replacing the running process is never intended here",
    "subprocess.getoutput": "use pypackhub.core.runner, which never uses a shell",
    "subprocess.getstatusoutput": "use pypackhub.core.runner, which never uses a shell",
    "eval": "eval executes arbitrary code; there is no safe use for it here",
    "exec": "exec executes arbitrary code; there is no safe use for it here",
    "pickle.load": "pickle can execute arbitrary code while loading",
    "pickle.loads": "pickle can execute arbitrary code while loading",
    "marshal.load": "marshal loads code objects",
    "marshal.loads": "marshal loads code objects",
    "yaml.load": "yaml.load can execute arbitrary code; use yaml.safe_load",
    "yaml.unsafe_load": "use yaml.safe_load",
    "yaml.full_load": "use yaml.safe_load; full_load still builds arbitrary objects",
    "tempfile.mktemp": "mktemp is subject to a race; use tempfile.mkstemp or TemporaryDirectory",
}

#: Keyword arguments that are refused wherever they appear.
BANNED_KEYWORDS: Final = {
    "shell": (
        "True",
        "shell=True hands a string to a shell; the runner always passes an argument list",
    ),
    "verify": ("False", "disabling TLS verification must never happen"),
}


@dataclass(frozen=True, slots=True)
class Finding:
    """One rule violation."""

    path: Path
    line: int
    rule: str
    message: str

    def render(self, root: Path) -> str:
        try:
            shown = self.path.relative_to(root)
        except ValueError:
            shown = self.path

        return f"{shown}:{self.line}: {self.rule}: {self.message}"


def _dotted_name(node: ast.expr, aliases: dict[str, str]) -> str | None:
    """Render a call target as a dotted name, following import aliases."""

    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)

    if isinstance(node, ast.Attribute):
        parent = _dotted_name(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"

    return None


def _collect_aliases(tree: ast.AST) -> dict[str, str]:
    """Map local names to the dotted path they really refer to.

    ``from os import system`` makes ``system(...)`` mean ``os.system(...)``; a
    scanner that only looked at attribute access would miss it.
    """

    aliases: dict[str, str] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                aliases[alias.asname or root] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.module is None:
                continue
            for alias in node.names:
                aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"

    return aliases


def _check_imports(tree: ast.AST, path: Path, root: Path) -> list[Finding]:
    findings: list[Finding] = []
    allowed = {str((root / allowed_path).resolve()) for allowed_path in SUBPROCESS_ALLOWED_FILES}

    if str(path.resolve()) in allowed:
        return findings

    for node in ast.walk(tree):
        modules: list[tuple[str, int]] = []

        if isinstance(node, ast.Import):
            modules = [(alias.name, node.lineno) for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [(node.module, node.lineno)]

        for module, lineno in modules:
            if module.split(".")[0] == "subprocess":
                findings.append(
                    Finding(
                        path=path,
                        line=lineno,
                        rule="spawn-outside-runner",
                        message=(
                            "only the hardened spawn sites (pypackhub.core.runner "
                            "and its elevated helper) may import subprocess; "
                            "everything else has to go through them"
                        ),
                    )
                )

    return findings


def scan_file(path: Path, root: Path) -> list[Finding]:
    """Scan one Python file and return what it violates."""

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as error:
        return [Finding(path=path, line=0, rule="unparsable", message=str(error))]

    aliases = _collect_aliases(tree)
    findings = _check_imports(tree, path, root)

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        target = _dotted_name(node.func, aliases)
        if target is not None and target in BANNED_CALLS:
            findings.append(
                Finding(
                    path=path,
                    line=node.lineno,
                    rule=f"banned-call:{target}",
                    message=BANNED_CALLS[target],
                )
            )

        for keyword in node.keywords:
            banned = BANNED_KEYWORDS.get(keyword.arg or "")
            if banned is None:
                continue
            value, message = banned
            if isinstance(keyword.value, ast.Constant) and repr(keyword.value.value) == value:
                findings.append(
                    Finding(
                        path=path,
                        line=node.lineno,
                        rule=f"banned-keyword:{keyword.arg}={value}",
                        message=message,
                    )
                )

    return findings


def iter_python_files(root: Path, targets: tuple[str, ...]) -> list[Path]:
    """Every Python file under the given target directories."""

    files: list[Path] = []

    for target in targets:
        directory = root / target
        if not directory.is_dir():
            continue

        files.extend(sorted(directory.rglob("*.py")))

    return files


def scan(root: Path, targets: tuple[str, ...] = DEFAULT_TARGETS) -> list[Finding]:
    """Scan a tree and return every finding."""

    findings: list[Finding] = []

    for path in iter_python_files(root, targets):
        findings.extend(scan_file(path, root))

    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="security_scan",
        description="Fail when a banned pattern appears in PyPackHub's own source.",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=REPOSITORY_ROOT,
        help="repository root to scan (default: the checkout this script lives in)",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        help="directory to scan, relative to --root; may be repeated",
    )

    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    targets = tuple(arguments.targets) if arguments.targets else DEFAULT_TARGETS

    findings = scan(root, targets)

    if not findings:
        scanned = len(iter_python_files(root, targets))
        print(f"security scan clean: {scanned} files, no banned patterns")
        return 0

    for finding in findings:
        print(finding.render(root), file=sys.stderr)

    print(f"security scan failed: {len(findings)} findings", file=sys.stderr)

    return 1


if __name__ == "__main__":  # pragma: no cover - exercised through the CLI test
    raise SystemExit(main())
