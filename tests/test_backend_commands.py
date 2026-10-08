"""The backend command contract: plan, then execute the plan.

SPEC section 11.7 requires the command a user confirms to be the same data that is
executed, so the plan is built first and shown, and execution only ever runs a
plan. These tests pin that down for both backends: what the argument list is, that
hostile input is refused before a plan exists, and that nothing reaches the runner
except through the plan path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pypackhub.core.backends.base import (
    ActionKind,
    BackendCapability,
    CommandPlan,
    PackageAction,
    UnsupportedActionError,
    iter_display_lines,
)
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.uv import UvBackend
from pypackhub.core.runner import CommandResult, format_argument
from pypackhub.core.secrets import redact
from pypackhub.core.validation import ValidationError

ENVIRONMENT = Path("/env")


@pytest.fixture
def pip_backend(tmp_path: Path) -> PipBackend:
    return PipBackend(executable=tmp_path / "pip", prefix=tmp_path)


@pytest.fixture
def uv_backend(tmp_path: Path) -> UvBackend:
    return UvBackend(executable=tmp_path / "uv", python=tmp_path / "python")


def _action(kind: ActionKind, **kwargs: object) -> PackageAction:
    return PackageAction(kind=kind, environment=ENVIRONMENT, **kwargs)  # type: ignore[arg-type]


# -- validation ----------------------------------------------------------------


def test_option_injection_in_a_name_is_refused() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.UNINSTALL, names=("--index-url=http://evil",))


def test_option_injection_in_a_spec_is_refused() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.INSTALL, specs=("-r /etc/passwd",))


def test_shell_metacharacters_are_refused() -> None:
    for hostile in ("foo; ls", "foo && whoami", "$(id)", "foo|bar", "`id`"):
        with pytest.raises(ValidationError):
            _action(ActionKind.INSTALL, specs=(hostile,))


def test_a_version_that_is_not_a_version_is_refused() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.DOWNGRADE, names=("flask",), version="1.0; rm -rf /")


def test_an_install_without_a_spec_is_refused() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.INSTALL)


def test_an_uninstall_without_a_name_is_refused() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.UNINSTALL)


def test_user_flag_does_not_apply_to_uninstall() -> None:
    with pytest.raises(ValidationError):
        _action(ActionKind.UNINSTALL, names=("flask",), user=True)


def test_a_valid_spec_is_normalised() -> None:
    # Whitespace is only tolerated around specifier punctuation, and is then
    # dropped: the value that reaches argv is the compact, canonical form.
    action = _action(ActionKind.INSTALL, specs=("Flask >= 2",))
    assert action.specs == ("Flask>=2",)


def test_display_target_names_what_is_affected() -> None:
    assert _action(ActionKind.UPGRADE, names=("flask", "click")).display_target() == "flask, click"


# -- pip -----------------------------------------------------------------------


def test_pip_install_plan(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.INSTALL, specs=("flask>=2",)))
    assert plan.argv == (str(tmp_path / "pip"), "install", "--", "flask>=2")
    assert plan.backend == "pip"
    assert plan.action.kind is ActionKind.INSTALL


def test_pip_install_uses_the_bound_interpreter(tmp_path: Path) -> None:
    """The plan must target the environment, not whatever pip is on PATH."""
    backend = PipBackend(prefix=tmp_path, python=tmp_path / "bin" / "python")
    plan = backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv[:4] == (
        str(tmp_path / "bin" / "python"),
        "-m",
        "pip",
        "install",
    )


def test_pip_uninstall_plan_never_prompts(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv == (str(tmp_path / "pip"), "uninstall", "--yes", "--", "flask")


def test_pip_upgrade_plan(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.UPGRADE, names=("flask",)))
    assert plan.argv == (
        str(tmp_path / "pip"),
        "install",
        "--upgrade",
        "--",
        "flask",
    )


def test_pip_downgrade_pins_the_version(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.DOWNGRADE, names=("flask",), version="2.0.0"))
    assert plan.argv[-1] == "flask==2.0.0"
    assert plan.argv[1:3] == ("install", "--")


def test_pip_upgrade_all_is_one_command(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("flask", "click")))
    assert plan.argv == (
        str(tmp_path / "pip"),
        "install",
        "--upgrade",
        "--",
        "flask",
        "click",
    )


def test_pip_user_install(pip_backend: PipBackend, tmp_path: Path) -> None:
    plan = pip_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))
    assert plan.argv == (str(tmp_path / "pip"), "install", "--user", "--", "flask")


def test_the_display_command_matches_the_argument_list(pip_backend: PipBackend) -> None:
    """The shown command is a rendering of the exact argv, not a fresh guess."""
    plan = pip_backend.plan(_action(ActionKind.INSTALL, specs=("flask>=2",)))
    rendered = " ".join(format_argument(argument) for argument in plan.argv)
    assert plan.display_command() == redact(rendered)
    assert plan.display_command().endswith('install -- "flask>=2"')


# -- uv ------------------------------------------------------------------------


def test_uv_plan_targets_the_interpreter(uv_backend: UvBackend, tmp_path: Path) -> None:
    plan = uv_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv == (
        str(tmp_path / "uv"),
        "pip",
        "install",
        "--python",
        str(tmp_path / "python"),
        "--",
        "flask",
    )


def test_uv_refuses_user_installs(uv_backend: UvBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        uv_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


def test_uv_uninstall_plan(uv_backend: UvBackend, tmp_path: Path) -> None:
    plan = uv_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv[:3] == (str(tmp_path / "uv"), "pip", "uninstall")
    assert "--" in plan.argv
    assert plan.argv[-1] == "flask"


# -- capabilities and unsupported actions --------------------------------------


def test_a_backend_refuses_an_action_it_does_not_support(pip_backend: PipBackend) -> None:
    narrow = _NarrowBackend(pip_backend)
    with pytest.raises(UnsupportedActionError):
        narrow.plan(_action(ActionKind.INSTALL, specs=("flask",)))


def test_user_install_is_refused_when_the_backend_cannot_do_it(pip_backend: PipBackend) -> None:
    no_user = _NarrowBackend(pip_backend, can_install_user=False, can_install=True)
    with pytest.raises(UnsupportedActionError):
        no_user.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


class _NarrowBackend(PipBackend):
    """A pip backend with a capability set narrowed for the test."""

    def __init__(
        self,
        inner: PipBackend,
        *,
        can_install: bool = False,
        can_install_user: bool = False,
    ) -> None:
        super().__init__()
        self._executable = inner._executable
        self._capability = BackendCapability(
            name="narrow",
            can_install=can_install,
            can_install_user=can_install_user,
        )

    @property
    def capability(self) -> BackendCapability:
        return self._capability

    def executable_path(self) -> Path:
        return Path("/narrow")


# -- execution -----------------------------------------------------------------


def test_execute_runs_the_plan_through_the_runner(
    pip_backend: PipBackend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The backend must not spawn anything itself: it must go through ``run``."""
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        calls.append((tuple(argv), kwargs))
        return CommandResult(
            argv=tuple(argv),
            returncode=0,
            stdout="installed\n",
            stderr="",
            duration=0.1,
        )

    monkeypatch.setattr("pypackhub.core.backends.base.run", fake_run)

    plan = pip_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    result = pip_backend.execute(plan, on_line=lambda _line: None)

    assert result.ok
    assert calls[0][0] == plan.argv
    assert set(calls[0][1]) >= {"timeout", "cancel", "on_stdout", "on_stderr"}


def test_display_lines_render_the_command_then_the_output() -> None:
    result = CommandResult(
        argv=("pip", "install", "--", "flask"),
        returncode=0,
        stdout="Collecting flask\n",
        stderr="",
        duration=0.1,
    )
    lines = list(iter_display_lines(result))
    assert lines[0] == "$ pip install -- flask"
    assert lines[1] == "Collecting flask"


def test_display_command_redacts_credentials() -> None:
    result = CommandResult(
        argv=(
            "pip",
            "install",
            "--index-url",
            "https://user:supersecret@example.test/simple",
            "--",
            "flask",
        ),
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )
    shown = result.display_command()
    assert "supersecret" not in shown
    assert "***" in shown


def test_a_plan_is_the_structure_a_dialog_shows_and_the_runner_runs() -> None:
    """``CommandPlan.display_command`` and the runner derive from one argv."""
    plan = CommandPlan(
        argv=("pip", "uninstall", "--yes", "--", "flask"),
        action=_action(ActionKind.UNINSTALL, names=("flask",)),
        label="pip uninstall",
        backend="pip",
    )
    result = CommandResult(
        argv=plan.argv,
        returncode=0,
        stdout="",
        stderr="",
        duration=0.0,
    )
    assert plan.display_command() == result.display_command()
    assert plan.matches(
        CommandPlan(
            argv=plan.argv,
            action=plan.action,
            label="other label",
            backend="pip",
        )
    )
