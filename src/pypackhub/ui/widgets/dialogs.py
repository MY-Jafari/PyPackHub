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
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.backends.base import ActionKind, CommandPlan
from pypackhub.core.deps import RemovalPreview
from pypackhub.core.platform_guard import ModificationPolicy, Severity
from pypackhub.core.validation import ValidationError, validate_package_spec
from pypackhub.ui.i18n.reasons import reason_text
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator

__all__ = [
    "BlockedDialog",
    "ConfirmationDialog",
    "CreateEnvironmentDialog",
    "CreateEnvironmentRequest",
    "DowngradeDialog",
    "InstallDialog",
    "RemovalPreviewDialog",
    "action_button_key",
    "action_title_key",
]

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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plan = plan
        self._policy = policy
        self._translator = translator

        self.setObjectName("confirmationDialog")
        self.setModal(True)
        self.setMinimumWidth(560)

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
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

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
        self.command_view.setPlainText(plan.display_command())
        self.command_view.setFixedHeight(72)
        layout.addWidget(self.command_view)

        self.policy_label = QLabel(self)
        self.policy_label.setObjectName("policyNotice")
        self.policy_label.setWordWrap(True)
        self.policy_label.setVisible(False)
        layout.addWidget(self.policy_label)
        self._describe_policy()

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(
            translator.translate(action_button_key(plan.action.kind))
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
        self.setMinimumWidth(480)

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
        self.setMinimumWidth(540)

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
        self.setMinimumWidth(520)

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
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

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
        self.setMinimumWidth(460)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel(translator.translate(TranslationKey.INSTALL_TITLE), self)
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

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
        self.setMinimumWidth(420)

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
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
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
