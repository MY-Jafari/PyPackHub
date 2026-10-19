"""The dialogs a changing action goes through.

Three rules shape all of them (SPEC sections 6 and 11.7):

* nothing changes an environment without an explicit confirmation, and the
  confirmation shows the exact command and the environment it targets;
* the command shown is the plan's own rendering of its argument list, so what is
  approved is what runs - there is no second, prettier description that could
  disagree with it;
* a refusal explains itself, in the user's language, and says what to do instead.

Each dialog is a plain ``QDialog`` with its values exposed as attributes and one
small ``value()`` accessor, so a test can build it, inspect it and accept it
without a mouse.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import ActionKind, CommandPlan
from pypackhub.core.deps import RemovalPreview, RequirementConflict
from pypackhub.core.disk_usage import EnvironmentUsage
from pypackhub.core.platform_guard import ModificationPolicy, Severity
from pypackhub.core.trust import TyposquatWarning
from pypackhub.core.validation import (
    LineKind,
    ParsedRequirements,
    RequirementLine,
    ValidationError,
    validate_package_spec,
)
from pypackhub.ui.formatting import format_bytes
from pypackhub.ui.i18n.reasons import reason_text
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator

__all__ = [
    "BlockedDialog",
    "ConfirmationDialog",
    "CreateEnvironmentDialog",
    "CreateEnvironmentRequest",
    "DowngradeDialog",
    "ElevationNotice",
    "FallbackNotice",
    "InstallDialog",
    "RemovalPreviewDialog",
    "RollbackPreviewDialog",
    "action_button_key",
    "action_title_key",
]


@dataclass(frozen=True, slots=True)
class ElevationNotice:
    """What a confirmation has to say about a run that needs the OS's help.

    Carries the *backend* rather than a sentence: the dialog renders the
    explanation in the user's language, and the backend is the only thing
    about it that varies.
    """

    backend: str


@dataclass(frozen=True, slots=True)
class FallbackNotice:
    """What a confirmation has to say when another backend runs the action.

    *replaced* names the package manager that could not: the one the user
    chose, which cannot change what the interpreter imports for these
    packages. The backend that *will* run it is not carried - the plan's own
    command is the dialog's answer to "who runs this".
    """

    replaced: str


@dataclass(frozen=True, slots=True)
class BootstrapNotice:
    """What a confirmation has to say when the chosen manager is missing.

    The user's chosen package manager is not installed, so it has to be set up
    before the action can run through it. *backend* names the tool that is
    missing; *commands* are the exact commands that install it, shown above
    the action's own command in the preview - the approval covers the whole
    batch, one decision (SPEC section 11.7: the text shown is the data that
    runs).
    """

    backend: str
    commands: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TyposquatNotice:
    """What a confirmation has to say when the name may be a mistyping.

    Carries the typosquat module's answer; the dialog renders the warning
    ("did you mean X?") in the user's language, loud enough that it is read
    before the button next to it is pressed.
    """

    warning: TyposquatWarning


@dataclass(frozen=True, slots=True)
class ConflictNotice:
    """What a confirmation has to say about declared ranges this breaks.

    One entry per installed package whose declared requirement the version
    about to be installed does not satisfy. The sentences are built by the
    dialog so each language can say them its own way; the evidence travels.
    """

    entries: tuple[RequirementConflict, ...] = ()


#: The dialog title for each action.
_TITLE_KEYS: dict[ActionKind, TranslationKey] = {
    ActionKind.INSTALL: TranslationKey.CONFIRM_INSTALL_TITLE,
    ActionKind.UNINSTALL: TranslationKey.CONFIRM_UNINSTALL_TITLE,
    ActionKind.UPGRADE: TranslationKey.CONFIRM_UPGRADE_TITLE,
    ActionKind.DOWNGRADE: TranslationKey.CONFIRM_UPGRADE_TITLE,
    ActionKind.UPGRADE_ALL: TranslationKey.PACKAGES_UPGRADE_ALL,
}

#: The button that performs it. The label matches the action rather than saying
#: "OK", because "OK" is how a user ends up uninstalling something by accident.
_BUTTON_KEYS: dict[ActionKind, TranslationKey] = {
    ActionKind.INSTALL: TranslationKey.BUTTON_INSTALL,
    ActionKind.UNINSTALL: TranslationKey.BUTTON_UNINSTALL,
    ActionKind.UPGRADE: TranslationKey.BUTTON_UPGRADE,
    ActionKind.DOWNGRADE: TranslationKey.BUTTON_UPGRADE,
    ActionKind.UPGRADE_ALL: TranslationKey.PACKAGES_UPGRADE_ALL,
}


def action_title_key(kind: ActionKind) -> TranslationKey:
    return _TITLE_KEYS[kind]


def action_button_key(kind: ActionKind) -> TranslationKey:
    return _BUTTON_KEYS[kind]


class ConfirmationDialog(QDialog):
    """Shows the exact command and the environment, then asks.

    The dialog is deliberately not dismissible by pressing Escape without an
    answer being recorded: ``result()`` distinguishes "confirmed" from "declined"
    and the caller only acts on the former.
    """

    def __init__(
        self,
        *,
        plan: CommandPlan,
        policy: ModificationPolicy,
        translator: Translator,
        project_url: str = "",
        elevation: ElevationNotice | None = None,
        fallback: FallbackNotice | None = None,
        bootstrap: BootstrapNotice | None = None,
        typosquat: TyposquatNotice | None = None,
        conflicts: ConflictNotice | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._policy = policy
        self._translator = translator
        self._elevation = elevation
        self._fallback = fallback
        self._bootstrap = bootstrap
        self._typosquat = typosquat
        self._conflicts = conflicts

        self.setObjectName("confirmationDialog")
        self.setModal(True)
        self.setMinimumWidth(680)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        self.title_label = QLabel(translator.translate(action_title_key(plan.action.kind)), self)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setWordWrap(True)
        layout.addWidget(self.title_label)

        self.warning_label = QLabel(
            translator.translate(
                TranslationKey.CONFIRM_WARNING,
                environment=str(plan.action.environment),
            ),
            self,
        )
        self.warning_label.setObjectName("secondaryText")
        self.warning_label.setWordWrap(True)
        layout.addWidget(self.warning_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeading)

        self.environment_label = QLabel(str(plan.action.environment), self)
        self.environment_label.setObjectName("versionLabel")
        self.environment_label.setWordWrap(True)
        form.addRow(
            translator.translate(TranslationKey.CONFIRM_ENVIRONMENT), self.environment_label
        )

        self.affected_label = QLabel(plan.action.display_target(), self)
        self.affected_label.setObjectName("versionLabel")
        self.affected_label.setWordWrap(True)
        form.addRow(translator.translate(TranslationKey.CONFIRM_AFFECTED), self.affected_label)
        layout.addLayout(form)

        command_header = QLabel(translator.translate(TranslationKey.CONFIRM_COMMAND), self)
        command_header.setObjectName("mutedText")
        layout.addWidget(command_header)

        self.command_view = QPlainTextEdit(self)
        self.command_view.setObjectName("commandPreview")
        self.command_view.setReadOnly(True)
        # With a bootstrap notice the preview carries the installing commands
        # above the action's own one; the user approves the whole batch, so
        # every line of it is shown.
        commands = (
            (*bootstrap.commands, plan.display_command())
            if bootstrap is not None
            else (plan.display_command(),)
        )
        self.command_view.setPlainText("\n".join(commands))
        # Four commands' worth of height, then scroll: a long restore batch
        # must not push the buttons off a small screen.
        self.command_view.setFixedHeight(min(72 * len(commands), 288))
        layout.addWidget(self.command_view)

        self.policy_label = QLabel(self)
        self.policy_label.setObjectName("policyNotice")
        self.policy_label.setWordWrap(True)
        self.policy_label.setVisible(False)
        layout.addWidget(self.policy_label)
        self._describe_policy()

        self.elevation_label = QLabel(self)
        self.elevation_label.setObjectName("policyNotice")
        self.elevation_label.setWordWrap(True)
        self.elevation_label.setVisible(False)
        layout.addWidget(self.elevation_label)

        self.fallback_label = QLabel(self)
        self.fallback_label.setObjectName("policyNotice")
        self.fallback_label.setWordWrap(True)
        self.fallback_label.setVisible(False)
        layout.addWidget(self.fallback_label)

        self.bootstrap_label = QLabel(self)
        self.bootstrap_label.setObjectName("policyNotice")
        self.bootstrap_label.setWordWrap(True)
        self.bootstrap_label.setVisible(False)
        layout.addWidget(self.bootstrap_label)

        # Supply-chain warnings render louder than the policy notices: a
        # typosquat is the one thing here that can be an attack, and the
        # safety-tinted notice is the strongest cue the theme has.
        self.typosquat_label = QLabel(self)
        self.typosquat_label.setObjectName("safetyNotice")
        self.typosquat_label.setWordWrap(True)
        self.typosquat_label.setVisible(False)
        layout.addWidget(self.typosquat_label)

        self.conflicts_label = QLabel(self)
        self.conflicts_label.setObjectName("policyNotice")
        self.conflicts_label.setWordWrap(True)
        self.conflicts_label.setVisible(False)
        layout.addWidget(self.conflicts_label)
        self._describe_elevation()
        self._describe_fallback()
        self._describe_bootstrap()
        self._describe_typosquat()
        self._describe_conflicts()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        # The elevation button wins when both notices apply: promising an
        # administrator run is the stronger consent, and both notices are
        # shown regardless.
        if elevation is not None:
            confirm_key = TranslationKey.CONFIRM_ELEVATION_BUTTON
        elif bootstrap is not None:
            confirm_key = TranslationKey.CONFIRM_BOOTSTRAP_BUTTON
        elif fallback is not None:
            confirm_key = TranslationKey.CONFIRM_FALLBACK_BUTTON
        else:
            confirm_key = action_button_key(plan.action.kind)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(confirm_key)
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        if plan.action.kind is ActionKind.UNINSTALL:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("dangerButton")
        else:
            self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        #: True once the user pressed the action button.
        self.confirmed = False

    @property
    def plan(self) -> CommandPlan:
        return self._plan

    @property
    def command_text(self) -> str:
        return self.command_view.toPlainText()

    @property
    def policy_text(self) -> str:
        return self.policy_label.text()

    @property
    def elevation_text(self) -> str:
        return self.elevation_label.text()

    @property
    def accepts_elevation(self) -> bool:
        """Whether the action button promises an administrator run."""
        return self._elevation is not None

    @property
    def fallback_text(self) -> str:
        return self.fallback_label.text()

    @property
    def accepts_fallback(self) -> bool:
        """Whether the action button names the backend that had to be substituted."""
        return self._fallback is not None

    @property
    def bootstrap_text(self) -> str:
        return self.bootstrap_label.text()

    @property
    def accepts_bootstrap(self) -> bool:
        """Whether the action button names the tool the batch installs first."""
        return self._bootstrap is not None

    def accept(self) -> None:
        self.confirmed = True
        super().accept()

    def _describe_policy(self) -> None:
        """Explain a warning or a danger before the user approves anything."""
        if self._policy.severity is Severity.OK:
            return

        language = self._translator.resolved_language
        explanation = reason_text(
            self._policy.reason_code, language, fallback=self._policy.explanation
        )
        text = explanation
        if self._policy.suggestion:
            suggestion = self._policy.suggestion
            label = self._translator.translate(TranslationKey.BLOCKED_SUGGESTION)
            text = f"{explanation}\n{label}: {suggestion}"

        self.policy_label.setText(text)
        self.policy_label.setVisible(True)

    def _describe_elevation(self) -> None:
        """Say, before anything is approved, what the elevated run means.

        SPEC section 11.5 in dialog form: why this one command needs the OS's
        help, that the help covers this action alone, and that nothing is
        persisted. The action button reads "Run as administrator" so the
        approval is for the elevation as well as for the command - an
        elevated run must never be consented to as a side effect of "Upgrade".
        """
        if self._elevation is None:
            return

        translator = self._translator
        text = "\n\n".join(
            (
                translator.translate(TranslationKey.CONFIRM_ELEVATION_TITLE),
                translator.translate(
                    TranslationKey.CONFIRM_ELEVATION_REASON, backend=self._elevation.backend
                ),
                translator.translate(TranslationKey.CONFIRM_ELEVATION_NOTE),
            )
        )

        self.elevation_label.setText(text)
        self.elevation_label.setVisible(True)

    def _describe_fallback(self) -> None:
        """Say that this command is another backend's, and why it had to be.

        The backend the user chose cannot change what the interpreter imports
        for these packages, so the same action was re-planned through the one
        backend that can. The substitution is the one thing here that must be
        consented to explicitly: the dialog shows the other backend's own
        exact command, and the action button names it (SPEC section 11.7 -
        the text shown is the data that runs).
        """
        if self._fallback is None:
            return

        translator = self._translator
        text = "\n\n".join(
            (
                translator.translate(TranslationKey.CONFIRM_FALLBACK_TITLE),
                translator.translate(
                    TranslationKey.CONFIRM_FALLBACK_REASON,
                    backend=self._fallback.replaced,
                    packages=self._plan.action.display_target(),
                ),
            )
        )

        self.fallback_label.setText(text)
        self.fallback_label.setVisible(True)

    def _describe_bootstrap(self) -> None:
        """Say that the chosen manager is missing and will be installed first.

        The user asked for a specific package manager, so substituting a
        different one silently is not an option - the tool they chose is set
        up first, then the action runs through it. The dialog explains that
        before anything is approved, the preview above shows every command
        the approval covers, and the action button names the tool it installs
        (SPEC section 11.7 - the text shown is the data that runs).
        """
        if self._bootstrap is None:
            return

        translator = self._translator
        text = "\n\n".join(
            (
                translator.translate(TranslationKey.CONFIRM_BOOTSTRAP_TITLE),
                translator.translate(
                    TranslationKey.CONFIRM_BOOTSTRAP_REASON, backend=self._bootstrap.backend
                ),
            )
        )

        self.bootstrap_label.setText(text)
        self.bootstrap_label.setVisible(True)

    def _describe_typosquat(self) -> None:
        """Say the name may be a mistyping of a popular package.

        The classic supply-chain trick is a name one letter away from
        something everybody installs, banking on the typo being repeated
        without being re-read. The warning shows the exact distance in
        words - "one letter away from X, did you mean X?" - because the way
        out is the reader noticing, not the tool refusing.
        """
        if self._typosquat is None:
            return

        translator = self._translator
        warning = self._typosquat.warning
        text = "\n\n".join(
            (
                translator.translate(TranslationKey.CONFIRM_TYPOSQUAT_TITLE),
                translator.translate(
                    TranslationKey.CONFIRM_TYPOSQUAT_REASON,
                    name=warning.name,
                    popular=warning.suspect_of,
                ),
            )
        )

        self.typosquat_label.setText(text)
        self.typosquat_label.setVisible(True)

    def _describe_conflicts(self) -> None:
        """List the declared requirements the incoming version breaks.

        These are facts the installed distributions state about themselves:
        who requires what range, and the version that is about to be
        installed anyway. Shown before approval so "yes" is an informed
        choice; the warnings are advisory, exactly like the spec's
        impact-warnings rule asks.
        """
        if self._conflicts is None or not self._conflicts.entries:
            return

        translator = self._translator
        lines = [
            translator.translate(
                TranslationKey.CONFLICT_LINE,
                package=conflict.package,
                requirement=conflict.requirement,
                version=conflict.version,
            )
            for conflict in self._conflicts.entries
        ]
        text = "\n\n".join((translator.translate(TranslationKey.CONFIRM_CONFLICT_TITLE), *lines))

        self.conflicts_label.setText(text)
        self.conflicts_label.setVisible(True)


class BlockedDialog(QDialog):
    """Explains why an action was refused, and what to do instead.

    When the refusal is the one the user can actually act on - a system or
    PEP 668 interpreter that only accepts packages inside a virtual environment -
    the dialog also offers to create one, and reports that through
    :attr:`wants_create_environment` (accepted-with-a-request rather than a
    plain "Close"). Anything else stays a dead end by design: there is no button
    that would talk the user into overriding a refusal.
    """

    def __init__(
        self,
        *,
        policy: ModificationPolicy,
        translator: Translator,
        offer_create_environment: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._policy = policy
        self._translator = translator
        self._wants_create = False
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.BLOCKED_TITLE), self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.reason_label = QLabel(
            reason_text(
                policy.reason_code,
                translator.resolved_language,
                fallback=policy.explanation,
            ),
            self,
        )
        self.reason_label.setObjectName("errorText")
        self.reason_label.setWordWrap(True)
        layout.addWidget(self.reason_label)

        self.suggestion_label = QLabel(self)
        self.suggestion_label.setObjectName("secondaryText")
        self.suggestion_label.setWordWrap(True)
        if policy.suggestion:
            self.suggestion_label.setText(
                f"{translator.translate(TranslationKey.BLOCKED_SUGGESTION)}: {policy.suggestion}"
            )
        else:
            self.suggestion_label.setVisible(False)
        layout.addWidget(self.suggestion_label)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setText(
            translator.translate(TranslationKey.BUTTON_CLOSE)
        )
        self.buttons.rejected.connect(self.reject)

        # The call-to-action is an extra button rather than a replacement for
        # Close, so declining the suggestion is still one obvious click.
        self.create_button: QPushButton | None = None
        if offer_create_environment:
            self.create_button = QPushButton(
                translator.translate(TranslationKey.BLOCKED_CREATE_ENVIRONMENT), self
            )
            self.create_button.setObjectName("primaryButton")
            self.create_button.clicked.connect(self._request_create)
            self.buttons.addButton(self.create_button, QDialogButtonBox.ButtonRole.AcceptRole)
        layout.addWidget(self.buttons)

    def _request_create(self) -> None:
        """The user took the offer; record it and close as accepted."""
        self._wants_create = True
        self.accept()

    @property
    def wants_create_environment(self) -> bool:
        return self._wants_create

    @property
    def reason_text_value(self) -> str:
        return self.reason_label.text()

    @property
    def suggestion_text(self) -> str:
        return self.suggestion_label.text()


class RollbackPreviewDialog(QDialog):
    """The preview of a restore, before the restore runs.

    SPEC section 4: "rollback with preview diff". This is deliberately *not*
    a confirmation dialog repeated twice - it is the one confirmation the
    restore gets, and it shows every exact command the restore will run, in
    order, so what is approved is what runs (SPEC section 11.7). Restoring is
    a bulk action that can uninstall things, so the preview states the
    removals as loudly as the reinstalls.
    """

    def __init__(
        self,
        *,
        restore_specs: tuple[str, ...],
        remove_names: tuple[str, ...],
        commands: tuple[str, ...],
        elevated: bool,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self.setModal(True)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.RESTORE_TITLE), self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        if restore_specs:
            restore_heading = QLabel(
                translator.translate(TranslationKey.RESTORE_REINTRODUCE, count=len(restore_specs)),
                self,
            )
            restore_heading.setObjectName("sectionHeading")
            restore_heading.setWordWrap(True)
            layout.addWidget(restore_heading)

            restore_view = QPlainTextEdit(self)
            restore_view.setObjectName("removalList")
            restore_view.setReadOnly(True)
            restore_view.setPlainText("\n".join(restore_specs))
            restore_view.setFixedHeight(min(160, 24 * max(1, len(restore_specs))))
            layout.addWidget(restore_view)

        if remove_names:
            remove_heading = QLabel(
                translator.translate(TranslationKey.RESTORE_REMOVE, count=len(remove_names)),
                self,
            )
            remove_heading.setObjectName("errorText")
            remove_heading.setWordWrap(True)
            layout.addWidget(remove_heading)

            remove_view = QPlainTextEdit(self)
            remove_view.setObjectName("removalList")
            remove_view.setReadOnly(True)
            remove_view.setPlainText("\n".join(remove_names))
            remove_view.setFixedHeight(min(120, 24 * max(1, len(remove_names))))
            layout.addWidget(remove_view)

        if elevated:
            elevation_note = QLabel(
                "\n\n".join(
                    (
                        translator.translate(TranslationKey.CONFIRM_ELEVATION_TITLE),
                        translator.translate(TranslationKey.CONFIRM_ELEVATION_NOTE),
                    )
                ),
                self,
            )
            elevation_note.setObjectName("policyNotice")
            elevation_note.setWordWrap(True)
            layout.addWidget(elevation_note)

        if commands:
            commands_heading = QLabel(translator.translate(TranslationKey.CONFIRM_COMMAND), self)
            commands_heading.setObjectName("mutedText")
            layout.addWidget(commands_heading)

            commands_view = QPlainTextEdit(self)
            commands_view.setObjectName("commandPreview")
            commands_view.setReadOnly(True)
            commands_view.setPlainText("\n".join(commands))
            commands_view.setFixedHeight(72 * max(1, len(commands)))
            layout.addWidget(commands_view)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(
                TranslationKey.CONFIRM_ELEVATION_BUTTON
                if elevated
                else TranslationKey.SNAPSHOTS_RESTORE
            )
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        #: True once the user pressed the restore button.
        self.confirmed = False

    def accept(self) -> None:
        self.confirmed = True
        super().accept()


class RemovalPreviewDialog(QDialog):
    """The dry run for a removal, shown before the confirmation that runs it.

    This is deliberately *not* the confirmation. The preview exists to be read -
    what goes, what is left behind, what the request would break - and it ends in
    Cancel or Continue. Continuing hands the same named packages to the normal
    confirm-and-run path, so the exact command is still shown and still approved
    separately. Two steps, because "what would happen" and "do it" are two
    different questions and merging them is how people uninstall an environment.
    """

    def __init__(
        self,
        *,
        preview: RemovalPreview,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._preview = preview
        self._translator = translator
        self.setModal(True)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.REMOVAL_PREVIEW_TITLE), self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.remove_heading = QLabel(self)
        self.remove_heading.setObjectName("sectionHeading")
        self.remove_heading.setText(
            translator.translate(
                TranslationKey.REMOVAL_PREVIEW_WOULD_REMOVE,
                count=len(preview.would_remove),
            )
        )
        layout.addWidget(self.remove_heading)

        self.remove_list = QPlainTextEdit(self)
        self.remove_list.setObjectName("removalList")
        self.remove_list.setReadOnly(True)
        self.remove_list.setPlainText("\n".join(preview.would_remove))
        layout.addWidget(self.remove_list)

        # Anything outside the request is stated, never silently applied.
        self.unused_label = self._note(
            TranslationKey.REMOVAL_PREVIEW_LEAVE_UNUSED,
            preview.would_leave_unused,
            layout,
        )
        self.blocked_label = self._note(
            TranslationKey.REMOVAL_PREVIEW_BLOCKED,
            preview.blocked,
            layout,
            error=True,
        )
        self.breaks_label = self._note(
            TranslationKey.REMOVAL_PREVIEW_BREAKS,
            preview.breaks,
            layout,
            error=True,
        )

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(TranslationKey.REMOVAL_PREVIEW_CONTINUE)
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _note(
        self,
        key: TranslationKey,
        names: tuple[str, ...],
        layout: QVBoxLayout,
        *,
        error: bool = False,
    ) -> QLabel:
        """Add a conditional note, hidden entirely when it has nothing to say."""
        label = QLabel(self)
        label.setObjectName("errorText" if error else "secondaryText")
        label.setWordWrap(True)
        if names:
            listing = ", ".join(names)
            label.setText(self._translator.translate(key, packages=listing))
        else:
            label.setVisible(False)
        layout.addWidget(label)
        return label

    @property
    def preview(self) -> RemovalPreview:
        return self._preview

    @property
    def remove_text(self) -> str:
        return self.remove_list.toPlainText()

    @property
    def unused_text(self) -> str:
        return self.unused_label.text()

    @property
    def blocked_text(self) -> str:
        return self.blocked_label.text()

    @property
    def breaks_text(self) -> str:
        return self.breaks_label.text()


@dataclass(frozen=True, slots=True)
class CreateEnvironmentRequest:
    """What the create-environment dialog collected.

    *location* is the folder the environment will live in; *interpreter* is the
    absolute path of the interpreter to build it with, or ``None`` for the one
    PyPackHub is running on.
    """

    location: str
    interpreter: str | None = None


class CreateEnvironmentDialog(QDialog):
    """Collects where to put a virtual environment and which interpreter to use.

    The dialog only gathers input: creating the environment is a real action that
    runs on a worker thread, streams into the log panel and can be cancelled, so
    it belongs with the rest of the changing actions - not in a modal that would
    freeze the window until ``venv`` finished.
    """

    def __init__(
        self,
        *,
        translator: Translator,
        default_location: str = ".venv",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator

        self.setModal(True)
        self.setMinimumWidth(600)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.CREATE_ENV_TITLE), self)
        title.setObjectName("pageTitle")
        title.setWordWrap(True)
        layout.addWidget(title)

        self.intro_label = QLabel(translator.translate(TranslationKey.CREATE_ENV_INTRO), self)
        self.intro_label.setObjectName("secondaryText")
        self.intro_label.setWordWrap(True)
        layout.addWidget(self.intro_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeading)

        self.location_edit = QLineEdit(self)
        self.location_edit.setText(default_location)
        self.location_edit.setPlaceholderText(".venv")
        self.location_edit.textChanged.connect(self._clear_error)
        form.addRow(translator.translate(TranslationKey.CREATE_ENV_LOCATION), self.location_edit)

        self.interpreter_edit = QLineEdit(self)
        self.interpreter_edit.setPlaceholderText(
            translator.translate(TranslationKey.CREATE_ENV_INTERPRETER_HINT)
        )
        self.interpreter_edit.textChanged.connect(self._clear_error)
        form.addRow(
            translator.translate(TranslationKey.CREATE_ENV_INTERPRETER), self.interpreter_edit
        )
        layout.addLayout(form)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(TranslationKey.CREATE_ENV_BUTTON)
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @property
    def location(self) -> str:
        return self.location_edit.text().strip()

    @property
    def interpreter(self) -> str:
        return self.interpreter_edit.text().strip()

    @property
    def error(self) -> str:
        return self.error_label.text()

    def value(self) -> CreateEnvironmentRequest:
        return CreateEnvironmentRequest(
            location=self.location,
            interpreter=self.interpreter or None,
        )

    def accept(self) -> None:
        """Refuse an empty folder or a relative interpreter before closing."""
        if not self.location:
            self._show_error(TranslationKey.CREATE_ENV_LOCATION_REQUIRED)
            return

        interpreter = self.interpreter
        if interpreter:
            from pathlib import Path

            # ``core.environments.create_environment`` refuses a relative path,
            # so the same rule is applied here where it can be explained.
            if not Path(interpreter).expanduser().is_absolute():
                self._show_error(TranslationKey.CREATE_ENV_INTERPRETER_ABSOLUTE)
                return

        super().accept()

    def _show_error(self, key: TranslationKey) -> None:
        self.error_label.setText(self._translator.translate(key))
        self.error_label.setVisible(True)

    def _clear_error(self) -> None:
        self.error_label.setVisible(False)


@dataclass(frozen=True, slots=True)
class InstallRequest:
    """What the install dialog collected."""

    spec: str
    user: bool = False


class InstallDialog(QDialog):
    """Collects a package specifier, validating it before the dialog closes.

    Validation happens here, with the same validator the backend will use, so an
    invalid or hostile specifier never reaches a plan - and the error is shown in
    the dialog rather than in a log the user has to read.
    """

    def __init__(
        self,
        *,
        translator: Translator,
        allow_user: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._allow_user = allow_user
        self._error_code = ""

        self.setModal(True)
        self.setMinimumWidth(540)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.INSTALL_TITLE), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeading)

        self.spec_edit = QLineEdit(self)
        self.spec_edit.setPlaceholderText(
            translator.translate(TranslationKey.PACKAGES_INSTALL_SPEC)
        )
        self.spec_edit.textChanged.connect(self._clear_error)
        form.addRow(translator.translate(TranslationKey.PACKAGES_INSTALL_SPEC), self.spec_edit)

        self.user_check = QCheckBox(
            translator.translate(TranslationKey.PACKAGES_INSTALL_USER), self
        )
        self.user_check.setEnabled(allow_user)
        form.addRow("", self.user_check)
        layout.addLayout(form)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(TranslationKey.BUTTON_INSTALL)
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setObjectName("primaryButton")
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @property
    def spec(self) -> str:
        return self.spec_edit.text().strip()

    @property
    def wants_user(self) -> bool:
        return self.user_check.isChecked() and self._allow_user

    @property
    def error(self) -> str:
        return self.error_label.text()

    @property
    def validated(self) -> str:
        """The validated spec, or an empty string when the input is refused."""
        try:
            return validate_package_spec(self.spec)
        except ValidationError:
            return ""

    def value(self) -> InstallRequest:
        return InstallRequest(spec=self.validated, user=self.wants_user)

    def accept(self) -> None:
        try:
            validate_package_spec(self.spec)
        except ValidationError as error:
            self._show_error(error)
            return

        super().accept()

    def _show_error(self, error: ValidationError) -> None:
        self._error_code = str(error.reason)
        self.error_label.setText(
            reason_text(
                str(error.reason),
                self._translator.resolved_language,
                fallback=str(error),
            )
        )
        self.error_label.setVisible(True)

    def _clear_error(self) -> None:
        self._error_code = ""
        self.error_label.setVisible(False)


class DowngradeDialog(QDialog):
    """Collects a target version for a downgrade."""

    def __init__(
        self,
        *,
        package: str,
        current_version: str,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._package = package
        self._translator = translator

        self.setModal(True)
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.DOWNGRADE_TITLE, package=package), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self.current_label = QLabel(
            translator.translate(TranslationKey.DOWNGRADE_CURRENT, version=current_version),
            self,
        )
        self.current_label.setObjectName("secondaryText")
        layout.addWidget(self.current_label)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeading)
        self.version_edit = QLineEdit(self)
        self.version_edit.textChanged.connect(self._clear_error)
        form.addRow(translator.translate(TranslationKey.DOWNGRADE_VERSION), self.version_edit)
        layout.addLayout(form)

        self.error_label = QLabel(self)
        self.error_label.setObjectName("errorText")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(TranslationKey.BUTTON_UPGRADE)
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(
            translator.translate(TranslationKey.BUTTON_CANCEL)
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    @property
    def version(self) -> str:
        return self.version_edit.text().strip()

    @property
    def error(self) -> str:
        return self.error_label.text()

    def accept(self) -> None:
        from packaging.version import InvalidVersion, Version

        try:
            Version(self.version)
        except InvalidVersion:
            self.error_label.setText(
                reason_text(
                    "invalid_version",
                    self._translator.resolved_language,
                    fallback=self.version,
                )
            )
            self.error_label.setVisible(True)
            return

        super().accept()

    def _clear_error(self) -> None:
        self.error_label.setVisible(False)


class ImportPreviewDialog(QDialog):
    """The review an imported requirements file goes through (SPEC 11.2).

    The file is untrusted input and the parser has already made a decision
    about every line; this dialog is where the human ratifies those
    decisions. Plain package lines start checked, anything the parser marked
    for review starts unchecked with its reason, and an invalid line cannot
    be selected at all. What the dialog hands back is what the user chose -
    never "the whole file", and never a line the parser refused.
    """

    def __init__(
        self,
        *,
        parsed: ParsedRequirements,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translator = translator
        self._parsed = parsed
        self.setObjectName("importDialog")
        self.setModal(True)
        self.setMinimumWidth(640)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.IMPORT_TITLE), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        source = QLabel(
            f"{translator.translate(TranslationKey.IMPORT_FILE)}: {parsed.source}", self
        )
        source.setObjectName("mutedText")
        source.setWordWrap(True)
        layout.addWidget(source)

        hint = QLabel(translator.translate(TranslationKey.IMPORT_SELECT), self)
        hint.setObjectName("secondaryText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.list_widget = QListWidget(self)
        self.list_widget.setObjectName("importList")
        # The lines the list actually shows, in list order: comments are
        # skipped below, so list rows and ``parsed.lines`` indices diverge
        # the moment a file has a comment - this list is the row -> line
        # mapping, and it is the only one consulted on the way back out.
        self._rows: list[RequirementLine] = []
        for line in parsed.lines:
            if line.kind is LineKind.COMMENT:
                continue  # the file's own words are not a decision to ratify
            item = QListWidgetItem(line.raw)
            if line.kind is LineKind.INVALID:
                # Untouchable: the parser refused this line, and the review
                # must not be able to un-refuse it.
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                if line.reason:
                    item.setToolTip(line.reason)
            elif line.is_package:
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if not line.requires_review else Qt.CheckState.Unchecked
                )
                if line.requires_review and line.reason:
                    item.setToolTip(line.reason)
            else:
                # An option line (-r, --index-url, ...): shown with its reason,
                # deliberately inert. This flow installs specs, and an
                # installer option cannot be honoured by installing specs -
                # a checkbox that changes nothing is a lie, so there is none.
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                if line.reason:
                    item.setToolTip(line.reason)
            self.list_widget.addItem(item)
            self._rows.append(line)
        self.list_widget.itemChanged.connect(lambda _item: self._update_footer())
        layout.addWidget(self.list_widget, 1)

        self.count_label = QLabel(self)
        self.count_label.setObjectName("mutedText")
        layout.addWidget(self.count_label)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._update_footer()

    def selected_specs(self) -> tuple[str, ...]:
        """The specs the user chose, in file order - the installable subset."""
        chosen: list[str] = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if (
                item is not None
                and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
                and item.checkState() is Qt.CheckState.Checked
            ):
                # Through the row -> line map, never the parsed file's own
                # indices: a comment anywhere above shifts those.
                line = self._rows[row]
                if line.is_package and line.value:
                    chosen.append(line.value)
        return tuple(chosen)

    def _update_footer(self) -> None:
        translator = self._translator
        selected = self.selected_specs()
        selectable = len(selected) + sum(
            1
            for row in range(self.list_widget.count())
            if (item := self.list_widget.item(row)) is not None
            and item.flags() & Qt.ItemFlag.ItemIsUserCheckable
            and item.checkState() is not Qt.CheckState.Checked
        )

        self.count_label.setText(
            translator.translate(
                TranslationKey.IMPORT_LINE_COUNT, count=len(selected), total=selectable
            )
        )
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if ok is not None:
            ok.setText(
                translator.translate(TranslationKey.IMPORT_CONFIRM, count=len(selected))
                if selected
                else translator.translate(TranslationKey.IMPORT_NOTHING_SELECTED)
            )
            ok.setEnabled(bool(selected))


class DiskUsageDialog(QDialog):
    """A read-only disk-usage analysis of one environment.

    Everything on the page was measured, none of it is guessed, and nothing
    here deletes anything: cache cleaning is a change to the machine and
    belongs behind a confirmation like every other change. The bars are
    proportioned against the largest measured package, so the eye can rank
    consumers without reading a number.
    """

    def __init__(
        self,
        *,
        usage: EnvironmentUsage,
        translator: Translator,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("diskDialog")
        self.setModal(True)
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.DISK_TITLE), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        total = QLabel(
            f"{translator.translate(TranslationKey.DISK_TOTAL)}: "
            f"{format_bytes(usage.total_bytes)}  \u00b7  "
            f"{translator.translate(TranslationKey.DISK_FILES, count=usage.files)}",
            self,
        )
        total.setObjectName("versionLabel")
        layout.addWidget(total)

        if not usage.complete:
            floor = QLabel(translator.translate(TranslationKey.DISK_INCOMPLETE), self)
            floor.setObjectName("mutedText")
            floor.setWordWrap(True)
            layout.addWidget(floor)

        largest_header = QLabel(translator.translate(TranslationKey.DISK_LARGEST), self)
        largest_header.setObjectName("secondaryText")
        layout.addWidget(largest_header)

        widest = usage.packages[0].size_bytes if usage.packages else 0
        for package in usage.packages:
            row = QHBoxLayout()
            name = QLabel(package.name, self)
            name.setMinimumWidth(160)
            row.addWidget(name)
            bar = QProgressBar(self)
            bar.setRange(0, max(widest, 1))
            bar.setValue(package.size_bytes)
            bar.setTextVisible(False)
            bar.setFixedHeight(8)
            row.addWidget(bar, 1)
            size = QLabel(format_bytes(package.size_bytes), self)
            size.setObjectName("mutedText")
            size.setMinimumWidth(80)
            row.addWidget(size)
            layout.addLayout(row)

        caches_header = QLabel(translator.translate(TranslationKey.DISK_CACHES), self)
        caches_header.setObjectName("secondaryText")
        layout.addWidget(caches_header)

        for cache in usage.caches:
            value = (
                format_bytes(cache.size_bytes)
                if cache.exists
                else translator.translate(TranslationKey.DISK_CACHE_MISSING)
            )
            cache_line = QLabel(f"{cache.kind}: {value}  \u00b7  {cache.path}", self)
            cache_line.setObjectName("mutedText")
            cache_line.setWordWrap(True)
            layout.addWidget(cache_line)

        layout.addStretch(1)

        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        close.accepted.connect(self.accept)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
