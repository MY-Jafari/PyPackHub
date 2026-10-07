## What this changes

<!-- One or two sentences. What problem does it solve, and for whom? -->

## Why

<!-- The reason behind the approach, especially if there were alternatives. -->

## How it was verified

<!--
Paste what you actually ran. "It compiles" is not verification.
Example:

    ruff check .            -> clean
    black --check .         -> clean
    mypy                    -> Success: no issues found
    pytest                  -> 42 passed
    bandit -c pyproject.toml -r src -> clean
-->

## Checklist

- [ ] `ruff check .`, `black --check .`, `mypy`, `pytest` and `bandit` all pass.
- [ ] Tests cover the new behaviour, including the failure cases.
- [ ] No new subprocess call site outside `pypackhub.core.runner`.
- [ ] Nothing in `pypackhub.core` imports a GUI toolkit or `pypackhub.ui`.
- [ ] All external input introduced by this change is validated before use.
- [ ] No string is hard-coded in the UI; both `en.json` and `fa.json` were updated.
- [ ] `CHANGELOG.md` updated under `Unreleased`.
- [ ] Dependency locks regenerated with `bash scripts/generate_locks.sh` (if dependencies changed).
- [ ] No credentials, tokens, private index URLs or personal paths are included in this diff.

## Related issues

<!-- Closes #123 -->
