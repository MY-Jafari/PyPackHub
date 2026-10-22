"""The backend command contract: plan, then execute the plan.

SPEC section 11.7 requires the command a user confirms to be the same data that is
executed, so the plan is built first and shown, and execution only ever runs a
plan. These tests pin that down for both backends: what the argument list is, that
hostile input is refused before a plan exists, and that nothing reaches the runner
except through the plan path.
"""

from __future__ import annotations

import json as json_module
from pathlib import Path

import pytest

from pypackhub.core.backends.base import (
    ActionKind,
    BackendCapability,
    CommandPlan,
    PackageAction,
    UnsupportedActionError,
    UnsupportedBackendError,
    iter_display_lines,
)
from pypackhub.core.backends.conda import CondaBackend
from pypackhub.core.backends.pdm import PdmBackend
from pypackhub.core.backends.pip import PipBackend
from pypackhub.core.backends.pipenv import PipenvBackend
from pypackhub.core.backends.pipx import PipxBackend
from pypackhub.core.backends.poetry import PoetryBackend
from pypackhub.core.backends.uv import UvBackend
from pypackhub.core.platform_guard import EnvironmentKind, InterpreterFacts
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


# -- marker folders for the location guard --------------------------------------


def _poetry_project(tmp_path: Path) -> Path:
    project = tmp_path / "poetry-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n", encoding="utf-8")
    return project


def _pipenv_project(tmp_path: Path) -> Path:
    project = tmp_path / "pipenv-project"
    project.mkdir()
    (project / "Pipfile").write_text("[packages]\n", encoding="utf-8")
    return project


def _pdm_project(tmp_path: Path) -> Path:
    project = tmp_path / "pdm-project"
    project.mkdir()
    (project / "pyproject.toml").write_text("[tool.pdm]\n", encoding="utf-8")
    return project


def _conda_environment(tmp_path: Path) -> Path:
    prefix = tmp_path / "conda-env"
    (prefix / "conda-meta").mkdir(parents=True)
    return prefix


def _interpreter_prefix(tmp_path: Path) -> Path:
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    (prefix / "python.exe").touch()
    return prefix


# -- conda and mamba ------------------------------------------------------------

#: The fixture executables as strings: on Windows ``str(Path("/conda"))``
#: carries the platform's separator, and the assertions compare against the
#: same rendering the plan carries.
CONDA_EXE = str(Path("/conda"))
MAMBA_EXE = str(Path("/mamba"))
POETRY_EXE = str(Path("/poetry"))
PIPENV_EXE = str(Path("/pipenv"))
PDM_EXE = str(Path("/pdm"))
PIPX_EXE = str(Path("/pipx"))


@pytest.fixture
def conda_backend() -> CondaBackend:
    return CondaBackend(tool="conda", executable=Path(CONDA_EXE))


@pytest.fixture
def mamba_backend() -> CondaBackend:
    return CondaBackend(tool="mamba", executable=Path(MAMBA_EXE))


def test_conda_install_plan_targets_the_prefix(conda_backend: CondaBackend) -> None:
    """Every conda command names the environment; nothing is activated."""
    plan = conda_backend.plan(_action(ActionKind.INSTALL, specs=("flask>=3",)))
    assert plan.argv == (CONDA_EXE, "install", "--prefix", str(ENVIRONMENT), "--yes", "flask>=3")
    assert plan.backend == "conda"
    # conda has no ``--`` separator; the validators refuse option-shaped
    # specs before a plan exists, which is the whole defence.
    assert "--" not in plan.argv


def test_conda_uninstall_plan_never_prompts(conda_backend: CondaBackend) -> None:
    plan = conda_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv == (CONDA_EXE, "remove", "--prefix", str(ENVIRONMENT), "--yes", "flask")


def test_conda_upgrade_plan(conda_backend: CondaBackend) -> None:
    plan = conda_backend.plan(_action(ActionKind.UPGRADE, names=("flask",)))
    assert plan.argv[:5] == (CONDA_EXE, "update", "--prefix", str(ENVIRONMENT), "--yes")
    assert plan.argv[-1] == "flask"


def test_conda_downgrade_pins_the_version(conda_backend: CondaBackend) -> None:
    plan = conda_backend.plan(_action(ActionKind.DOWNGRADE, names=("flask",), version="2.0.0"))
    assert plan.argv == (
        CONDA_EXE,
        "install",
        "--prefix",
        str(ENVIRONMENT),
        "--yes",
        "flask==2.0.0",
    )


def test_conda_upgrade_all_uses_condas_own_flag(conda_backend: CondaBackend) -> None:
    plan = conda_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("everything",)))
    assert plan.argv == (CONDA_EXE, "update", "--prefix", str(ENVIRONMENT), "--yes", "--all")


def test_mamba_plans_run_mamba_and_say_so(mamba_backend: CondaBackend) -> None:
    """A user who asked for mamba confirms a mamba command, not a conda one."""
    plan = mamba_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv[0] == MAMBA_EXE
    assert plan.backend == "mamba"
    assert mamba_backend.capability.name == "mamba"
    assert mamba_backend.executable_name == "mamba"


def test_conda_refuses_user_installs(conda_backend: CondaBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        conda_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


def test_the_conda_family_rejects_other_tool_names() -> None:
    with pytest.raises(ValueError):
        CondaBackend(tool="rye")


def test_conda_only_supports_conda_environments(
    conda_backend: CondaBackend, tmp_path: Path
) -> None:
    assert conda_backend.supports_environment(_conda_environment(tmp_path))
    assert not conda_backend.supports_environment(_interpreter_prefix(tmp_path))


def test_conda_listing_parses_the_json_report(
    conda_backend: CondaBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """conda's report is the whole environment: conda packages *and* pip's."""
    stdout = json_module.dumps(
        [
            {"name": "numpy", "version": "1.26.0", "channel": "defaults"},
            {"name": "flask", "version": "3.0.0", "channel": "pypi"},
        ]
    )

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.conda.run", fake_run)
    listing = conda_backend.list_packages(ENVIRONMENT)

    assert listing.backend == "conda"
    assert [(package.name, package.specifier) for package in listing.packages] == [
        ("numpy", "1.26.0"),
        ("flask", "3.0.0"),
    ]


def test_conda_listing_reports_a_failure_verbatim(
    conda_backend: CondaBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(
            argv=(),
            returncode=1,
            stdout="",
            stderr="CondaError: nothing to do",
            duration=0.0,
        )

    monkeypatch.setattr("pypackhub.core.backends.conda.run", fake_run)
    listing = conda_backend.list_packages(ENVIRONMENT)

    assert listing.packages == ()
    assert "CondaError" in listing.error


# -- poetry ---------------------------------------------------------------------


@pytest.fixture
def poetry_backend() -> PoetryBackend:
    return PoetryBackend(executable=Path(POETRY_EXE))


def test_poetry_install_plan_runs_in_the_project(poetry_backend: PoetryBackend) -> None:
    """poetry finds its project by directory, so the plan says where to run."""
    plan = poetry_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv == (POETRY_EXE, "add", "flask")
    assert plan.backend == "poetry"
    assert plan.cwd == ENVIRONMENT


def test_poetry_remove_plan(poetry_backend: PoetryBackend) -> None:
    plan = poetry_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv == (POETRY_EXE, "remove", "flask")
    assert plan.cwd == ENVIRONMENT


def test_poetry_update_plan(poetry_backend: PoetryBackend) -> None:
    plan = poetry_backend.plan(_action(ActionKind.UPGRADE, names=("flask",)))
    assert plan.argv == (POETRY_EXE, "update", "flask")


def test_poetry_downgrade_pins_the_version(poetry_backend: PoetryBackend) -> None:
    plan = poetry_backend.plan(_action(ActionKind.DOWNGRADE, names=("flask",), version="2.0.0"))
    assert plan.argv == (POETRY_EXE, "add", "flask==2.0.0")


def test_poetry_update_all_is_a_bare_update(poetry_backend: PoetryBackend) -> None:
    plan = poetry_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("everything",)))
    assert plan.argv == (POETRY_EXE, "update")


def test_poetry_refuses_user_installs(poetry_backend: PoetryBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        poetry_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


def test_poetry_only_supports_poetry_projects(
    poetry_backend: PoetryBackend, tmp_path: Path
) -> None:
    assert poetry_backend.supports_environment(_poetry_project(tmp_path))
    assert not poetry_backend.supports_environment(_pipenv_project(tmp_path))
    assert not poetry_backend.supports_environment(_interpreter_prefix(tmp_path))


def test_poetry_listing_parses_show_lines(
    poetry_backend: PoetryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row counts only when the second token is a version; wrapped
    descriptions are not packages."""
    stdout = (
        "black 24.3.0 The uncompromising code formatter.\n"
        "  A maintained continuation of the original.\n"
        "click 8.1.7 Composable command line interface toolkit\n"
    )

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.poetry.run", fake_run)
    listing = poetry_backend.list_packages(ENVIRONMENT)

    assert [(package.name, package.specifier) for package in listing.packages] == [
        ("black", "24.3.0"),
        ("click", "8.1.7"),
    ]
    assert listing.error == ""


def test_poetry_output_without_rows_is_an_error_not_an_empty_project(
    poetry_backend: PoetryBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(
            argv=(), returncode=0, stdout="Warning: nothing shown\n", stderr="", duration=0.0
        )

    monkeypatch.setattr("pypackhub.core.backends.poetry.run", fake_run)
    listing = poetry_backend.list_packages(ENVIRONMENT)

    assert listing.packages == ()
    assert listing.error  # "nothing installed" and "could not read" differ


# -- pipenv ---------------------------------------------------------------------


@pytest.fixture
def pipenv_backend() -> PipenvBackend:
    return PipenvBackend(executable=Path(PIPENV_EXE))


def test_pipenv_install_plan_runs_pipenv_in_the_project(pipenv_backend: PipenvBackend) -> None:
    plan = pipenv_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv == (PIPENV_EXE, "install", "flask")
    assert plan.cwd == ENVIRONMENT


def test_pipenv_uninstall_plan(pipenv_backend: PipenvBackend) -> None:
    plan = pipenv_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv == (PIPENV_EXE, "uninstall", "flask")


def test_pipenv_upgrade_updates_and_records(pipenv_backend: PipenvBackend) -> None:
    plan = pipenv_backend.plan(_action(ActionKind.UPGRADE, names=("flask",)))
    assert plan.argv == (PIPENV_EXE, "update", "flask")


def test_pipenv_upgrade_all_is_a_bare_update(pipenv_backend: PipenvBackend) -> None:
    plan = pipenv_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("everything",)))
    assert plan.argv == (PIPENV_EXE, "update")


def test_pipenv_downgrade_pins_the_version(pipenv_backend: PipenvBackend) -> None:
    plan = pipenv_backend.plan(_action(ActionKind.DOWNGRADE, names=("flask",), version="2.0.0"))
    assert plan.argv == (PIPENV_EXE, "install", "flask==2.0.0")


def test_pipenv_refuses_user_installs(pipenv_backend: PipenvBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        pipenv_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


def test_pipenv_only_supports_pipfile_projects(
    pipenv_backend: PipenvBackend, tmp_path: Path
) -> None:
    assert pipenv_backend.supports_environment(_pipenv_project(tmp_path))
    assert not pipenv_backend.supports_environment(_poetry_project(tmp_path))


def test_pipenv_listing_runs_pip_inside_the_project(
    pipenv_backend: PipenvBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The report is pip's own JSON, read by pip's own parser."""
    seen: dict[str, object] = {}
    stdout = json_module.dumps([{"name": "requests", "version": "2.31.0"}])

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        seen["argv"] = tuple(argv)
        seen["cwd"] = kwargs.get("cwd")
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.pipenv.run", fake_run)
    listing = pipenv_backend.list_packages(ENVIRONMENT)

    assert seen["argv"] == (PIPENV_EXE, "run", "pip", "list", "--format", "json")
    assert seen["cwd"] == ENVIRONMENT
    assert listing.backend == "pipenv"
    assert [(package.name, package.specifier) for package in listing.packages] == [
        ("requests", "2.31.0")
    ]


# -- pdm ------------------------------------------------------------------------


@pytest.fixture
def pdm_backend() -> PdmBackend:
    return PdmBackend(executable=Path(PDM_EXE))


def test_pdm_install_plan_runs_in_the_project(pdm_backend: PdmBackend) -> None:
    plan = pdm_backend.plan(_action(ActionKind.INSTALL, specs=("flask",)))
    assert plan.argv == (PDM_EXE, "add", "flask")
    assert plan.cwd == ENVIRONMENT


def test_pdm_remove_plan(pdm_backend: PdmBackend) -> None:
    plan = pdm_backend.plan(_action(ActionKind.UNINSTALL, names=("flask",)))
    assert plan.argv == (PDM_EXE, "remove", "flask")


def test_pdm_update_plan(pdm_backend: PdmBackend) -> None:
    plan = pdm_backend.plan(_action(ActionKind.UPGRADE, names=("flask",)))
    assert plan.argv == (PDM_EXE, "update", "flask")


def test_pdm_downgrade_pins_the_version(pdm_backend: PdmBackend) -> None:
    plan = pdm_backend.plan(_action(ActionKind.DOWNGRADE, names=("flask",), version="2.0.0"))
    assert plan.argv == (PDM_EXE, "add", "flask==2.0.0")


def test_pdm_update_all_is_a_bare_update(pdm_backend: PdmBackend) -> None:
    plan = pdm_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("everything",)))
    assert plan.argv == (PDM_EXE, "update")


def test_pdm_refuses_user_installs(pdm_backend: PdmBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        pdm_backend.plan(_action(ActionKind.INSTALL, specs=("flask",), user=True))


def test_pdm_only_supports_pdm_projects(pdm_backend: PdmBackend, tmp_path: Path) -> None:
    assert pdm_backend.supports_environment(_pdm_project(tmp_path))
    assert not pdm_backend.supports_environment(_pipenv_project(tmp_path))


def test_pdm_listing_parses_the_table(
    pdm_backend: PdmBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headers and box-drawing rules are skipped by the version-shaped token
    guard, not by counting lines."""
    stdout = (
        "Name          Version   Location\n"
        "\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
        "certifi       2024.2.2  c:\\lib\\site-packages\n"
        "requests     2.31.0    c:\\lib\\site-packages\n"
    )

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.pdm.run", fake_run)
    listing = pdm_backend.list_packages(ENVIRONMENT)

    assert [(package.name, package.specifier) for package in listing.packages] == [
        ("certifi", "2024.2.2"),
        ("requests", "2.31.0"),
    ]
    assert listing.error == ""


def test_pdm_output_without_rows_is_an_error_not_an_empty_project(
    pdm_backend: PdmBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(
            argv=(), returncode=0, stdout="[hint]: no project\n", stderr="", duration=0.0
        )

    monkeypatch.setattr("pypackhub.core.backends.pdm.run", fake_run)
    listing = pdm_backend.list_packages(ENVIRONMENT)

    assert listing.packages == ()
    assert listing.error


# -- pipx -----------------------------------------------------------------------


@pytest.fixture
def pipx_backend() -> PipxBackend:
    return PipxBackend(executable=Path(PIPX_EXE))


def test_pipx_install_plan(pipx_backend: PipxBackend) -> None:
    plan = pipx_backend.plan(_action(ActionKind.INSTALL, specs=("black",)))
    assert plan.argv == (PIPX_EXE, "install", "black")


def test_pipx_uninstall_plan(pipx_backend: PipxBackend) -> None:
    plan = pipx_backend.plan(_action(ActionKind.UNINSTALL, names=("black",)))
    assert plan.argv == (PIPX_EXE, "uninstall", "black")


def test_pipx_upgrade_plan(pipx_backend: PipxBackend) -> None:
    plan = pipx_backend.plan(_action(ActionKind.UPGRADE, names=("black",)))
    assert plan.argv == (PIPX_EXE, "upgrade", "black")


def test_pipx_upgrade_all_plan(pipx_backend: PipxBackend) -> None:
    plan = pipx_backend.plan(_action(ActionKind.UPGRADE_ALL, specs=("everything",)))
    assert plan.argv == (PIPX_EXE, "upgrade-all")


def test_pipx_downgrade_is_refused_rather_than_approximated(pipx_backend: PipxBackend) -> None:
    """pipx has no command that re-pins a version; offering one would be a
    confirmation the tool cannot honour."""
    with pytest.raises(UnsupportedActionError):
        pipx_backend.plan(_action(ActionKind.DOWNGRADE, names=("black",), version="1.0.0"))


def test_pipx_refuses_user_installs(pipx_backend: PipxBackend) -> None:
    with pytest.raises(UnsupportedActionError):
        pipx_backend.plan(_action(ActionKind.INSTALL, specs=("black",), user=True))


def test_pipx_supports_any_location(pipx_backend: PipxBackend, tmp_path: Path) -> None:
    """pipx's scope is its own home, so the selected location is irrelevant."""
    assert pipx_backend.supports_environment(_interpreter_prefix(tmp_path))
    assert pipx_backend.supports_environment(_poetry_project(tmp_path))


def test_pipx_listing_parses_the_json_report(
    pipx_backend: PipxBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    stdout = json_module.dumps(
        {
            "venvs": {
                "black": {"main_package": {"package": "black", "package_version": "24.3.0"}},
                "unguarded": {"metadata": {}},
            },
            "pipx_spec_version": "0.1",
        }
    )

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.pipx.run", fake_run)
    listing = pipx_backend.list_packages(ENVIRONMENT)

    assert [(package.name, package.specifier) for package in listing.packages] == [
        ("black", "24.3.0"),
        ("unguarded", ""),  # still installed, version unreadable
    ]


def test_pipx_rows_live_in_the_pipx_home(
    pipx_backend: PipxBackend, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PIPX_HOME", str(tmp_path / "pipx-home"))
    stdout = json_module.dumps({"venvs": {"ruff": {"main_package": {"package_version": "0.3.0"}}}})

    def fake_run(argv: tuple[str, ...], **kwargs: object) -> CommandResult:
        del argv, kwargs
        return CommandResult(argv=(), returncode=0, stdout=stdout, stderr="", duration=0.0)

    monkeypatch.setattr("pypackhub.core.backends.pipx.run", fake_run)
    listing = pipx_backend.list_packages(ENVIRONMENT)

    home = tmp_path / "pipx-home"
    assert listing.environment == home
    assert listing.packages[0].location == home


def test_pipx_policy_never_demands_elevation(pipx_backend: PipxBackend) -> None:
    """pipx writes only in the user's own directories; the OS's help is never
    its to ask for, whatever environment happens to be selected."""
    facts = InterpreterFacts(
        prefix=ENVIRONMENT,
        executable=ENVIRONMENT / "python.exe",
        kind=EnvironmentKind.SYSTEM,
        externally_managed=True,
        externally_managed_marker=None,
        is_alias_stub=False,
    )
    policy = pipx_backend.policy_for(facts)

    assert policy.allowed is True
    assert policy.reason_code == "target.pipx_home"
    assert policy.severity.value == "ok"


# -- the location guard: a forced manager never runs where it cannot ----


def test_pip_refuses_a_project_folder(tmp_path: Path) -> None:
    """A PATH pip against a project folder would install into some other
    prefix while the confirmation showed an honest-looking command."""
    assert not PipBackend().supports_environment(_poetry_project(tmp_path))


def test_pip_accepts_an_interpreter_prefix(tmp_path: Path) -> None:
    assert PipBackend().supports_environment(_interpreter_prefix(tmp_path))


def test_uv_refuses_a_project_folder(tmp_path: Path) -> None:
    assert not UvBackend().supports_environment(_pdm_project(tmp_path))


def test_a_bound_interpreter_accepts_any_location() -> None:
    """The guard exists to stop *guessing* an interpreter; a bound one is a
    promise, not a guess."""
    assert PipBackend(python=Path("/py")).supports_environment(Path("/nowhere"))


def test_a_missing_project_tool_reports_its_own_absence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe's words are what the absence detection matches on; the
    backend's ``executable_path`` then wraps them in the "not available on
    this system" sentence the detection also knows."""
    from pypackhub.core.backends.pdm import find_pdm
    from pypackhub.core.backends.pipenv import find_pipenv
    from pypackhub.core.backends.pipx import find_pipx
    from pypackhub.core.backends.poetry import find_poetry
    from pypackhub.core.runner import ExecutableNotFoundError

    def refuse(name: str) -> Path:
        raise ExecutableNotFoundError(name)

    monkeypatch.setattr("pypackhub.core.runner.resolve_executable", refuse)

    for finder, message in (
        (find_poetry, "poetry could not be found"),
        (find_pipenv, "pipenv could not be found"),
        (find_pdm, "pdm could not be found"),
        (find_pipx, "pipx could not be found"),
    ):
        with pytest.raises(UnsupportedBackendError) as raised:
            finder()
        assert message in str(raised.value), str(raised.value)

    for executable_path in (
        PoetryBackend().executable_path,
        PipenvBackend().executable_path,
        PdmBackend().executable_path,
        PipxBackend().executable_path,
        CondaBackend().executable_path,
    ):
        with pytest.raises(UnsupportedBackendError) as raised:
            executable_path()
        assert "is not available on this system" in str(raised.value)
