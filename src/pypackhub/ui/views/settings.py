"""The settings page.

It renders the settings :class:`~pypackhub.core.settings.AppSettings` exposes,
lets the user change the ones that are safe to expose in a settings page, and
writes back through the :class:`~pypackhub.ui.views.window_shell.WindowShell` the
window owns so a change is validated, re-translated and re-themed in one place.

Nothing here reaches the core directly: it asks the shell for a settings snapshot,
shows it, and writes a validated copy back when the user applies. The shell is also
what re-translates and re-themes the window after a change, so the settings page
does not need to know about the rest of the window at all.

Every piece of chrome the page owns - the group titles, the row labels, the combo
entries, the spin-box suffix and the placeholders - is registered in a lookup so
:meth:`SettingsView.retranslate` can re-render all of it. A settings page that
half-switches language is worse than one that does not switch at all.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.inventory import (
    manager_available,
    manager_installable,
    manager_removable,
)
from pypackhub.core.settings import (
    AppSettings,
    ColorTheme,
    Language,
    validate_settings,
)
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.services import UiContext
from pypackhub.ui.views.window_shell import WindowShell
from pypackhub.ui.widgets.log_panel import RunOutcome

__all__ = ["PACKAGE_MANAGER_CHOICES", "SettingsView"]


def _repolish(widget: QWidget) -> None:
    """Re-evaluate a widget's property-based styling after a change.

    Qt resolves attribute selectors like ``[error="true"]`` when a widget
    is polished; setting the property afterwards does not restyle the
    widget on its own, which is what this forces.
    """
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


#: The package managers the user may force, in the order the combo offers them.
#: ``auto`` is first because it is the default and the one most users want;
#: the rest follow the order of the settings validator. The names are their
#: own product names, shown untranslated in every language.
PACKAGE_MANAGER_CHOICES: tuple[str, ...] = (
    "auto",
    "uv",
    "pip",
    "conda",
    "mamba",
    "poetry",
    "pipx",
    "pipenv",
    "pdm",
)

#: How wide a control on this page is before the layout stretches it. A combo
#: that only sizes itself to "uv" is a control nobody can aim at.
#: The settings fields share one minimum width so the rows read as a form.
#: Wide enough that a full sentence of description wraps within the field
#: column instead of stretching the group box sideways.
SETTINGS_FIELD_MIN_WIDTH: int = 280


class SettingsView(QWidget):
    """A page that edits the application settings the user is allowed to change.

    The page is re-translated by the window's ``retranslate`` pass, so every label
    follows the current language without the page having to know when that happens.
    """

    #: Emitted with the manager's name when the user picks one that is not
    #: installed and answers "install it now". The window routes it to the
    #: packages view, which owns the confirm-and-run path every change goes
    #: through - the install is recorded like any other action, not a quiet
    #: side effect of picking a combo entry.
    manager_install_requested = Signal(str)

    #: Emitted with the manager's name when the user clicks the remove
    #: button next to an installed, pip-removable manager. Routed the same
    #: way: the removal is a change to the environment, confirmed with its
    #: exact command and recorded like every other uninstall.
    manager_remove_requested = Signal(str)

    def __init__(
        self,
        *,
        shell: WindowShell,
        context: UiContext | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._shell = shell
        # The services the package-manager choice has to reach. Optional so the
        # page can still be built without one (as the test-suite does) - a page
        # with no context keeps its settings, it only cannot rewire the provider.
        self._context = context
        # Set while ``load_settings`` writes into the controls: it stops the
        # change handlers from reading half-populated fields and validating them.
        self._loading = False

        # Chrome, keyed by the translation key that produced it, so a language
        # switch can re-render every part of the page in one pass.
        self._groups: dict[TranslationKey, QGroupBox] = {}
        self._labels: dict[TranslationKey, QLabel] = {}
        self._line_edits: list[QLineEdit] = []

        # The page is taller than most windows, so it scrolls: a group that falls
        # off the bottom edge with no scrollbar is a setting the user cannot
        # reach at all.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("settingsScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._body = QWidget(self._scroll)
        self._body.setObjectName("settingsBody")
        self._scroll.setWidget(self._body)
        outer.addWidget(self._scroll, 1)

        layout = QFormLayout(self._body)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        # Fields grow with the page and the labels stay next to their control, so
        # the page reads as a form rather than as two columns that drift apart.
        layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        # -- appearance ----------------------------------------------------------

        appearance_layout = self._group(layout, TranslationKey.SETTINGS_TITLE)

        self._theme_combo = QComboBox(self._body)
        self._theme_combo.setObjectName("settingsCombo")
        self._theme_combo.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        for theme_member in ColorTheme:
            self._theme_combo.addItem(self._theme_label(theme_member), theme_member.value)
        appearance_layout.addRow(self._label(TranslationKey.SETTINGS_THEME), self._theme_combo)

        self._language_combo = QComboBox(self._body)
        self._language_combo.setObjectName("settingsCombo")
        self._language_combo.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        for language_member in Language:
            self._language_combo.addItem(
                self._language_label(language_member), language_member.value
            )
        appearance_layout.addRow(
            self._label(TranslationKey.SETTINGS_LANGUAGE), self._language_combo
        )

        self._theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self._language_combo.currentIndexChanged.connect(self._on_language_changed)

        # -- table ---------------------------------------------------------------

        table_layout = self._group(layout, TranslationKey.SETTINGS_TABLE)

        self._page_size = QSpinBox(self._body)
        self._page_size.setObjectName("settingsSpinBox")
        self._page_size.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        self._page_size.setMinimum(10)
        self._page_size.setMaximum(500)
        self._page_size.setSingleStep(10)
        self._page_size.setSuffix(" " + self._text(TranslationKey.SETTINGS_PACKAGES_PER_PAGE_UNITS))
        table_layout.addRow(self._label(TranslationKey.SETTINGS_PACKAGE_PAGE_SIZE), self._page_size)

        self._sidebar_width = QSpinBox(self._body)
        self._sidebar_width.setObjectName("settingsSpinBox")
        self._sidebar_width.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        self._sidebar_width.setMinimum(80)
        self._sidebar_width.setMaximum(600)
        self._sidebar_width.setSingleStep(10)
        self._sidebar_width.setSuffix(" px")
        table_layout.addRow(self._label(TranslationKey.SETTINGS_SIDEBAR_WIDTH), self._sidebar_width)

        # -- PyPI ----------------------------------------------------------------

        pypi_layout = self._group(layout, TranslationKey.SETTINGS_PYPI)

        self._index_url = self._line_edit(pypi_layout, TranslationKey.SETTINGS_PYPI_INDEX)
        self._index_url.setPlaceholderText("https://pypi.org/simple")

        self._extra_index_urls = self._line_edit(
            pypi_layout, TranslationKey.SETTINGS_PYPI_EXTRA_INDEX_URLS
        )
        self._trusted_hosts = self._line_edit(
            pypi_layout, TranslationKey.SETTINGS_PYPI_TRUSTED_HOSTS
        )

        # -- behaviour -----------------------------------------------------------

        behaviour_layout = self._group(layout, TranslationKey.SETTINGS_BEHAVIOUR)

        self._confirm_install = QCheckBox(self._body)
        self._confirm_install.setObjectName("settingsCheckBox")
        behaviour_layout.addRow(
            self._label(TranslationKey.SETTINGS_CONFIRM_INSTALL), self._confirm_install
        )

        self._confirm_uninstall = QCheckBox(self._body)
        self._confirm_uninstall.setObjectName("settingsCheckBox")
        behaviour_layout.addRow(
            self._label(TranslationKey.SETTINGS_CONFIRM_UNINSTALL), self._confirm_uninstall
        )

        self._confirm_upgrade = QCheckBox(self._body)
        self._confirm_upgrade.setObjectName("settingsCheckBox")
        behaviour_layout.addRow(
            self._label(TranslationKey.SETTINGS_CONFIRM_UPGRADE), self._confirm_upgrade
        )

        self._show_sizes = QCheckBox(self._body)
        self._show_sizes.setObjectName("settingsCheckBox")
        behaviour_layout.addRow(self._label(TranslationKey.SETTINGS_SHOW_SIZES), self._show_sizes)

        self._show_outdated = QCheckBox(self._body)
        self._show_outdated.setObjectName("settingsCheckBox")
        behaviour_layout.addRow(
            self._label(TranslationKey.SETTINGS_SHOW_OUTDATED), self._show_outdated
        )

        self._package_manager = QComboBox(self._body)
        self._package_manager.setObjectName("settingsCombo")
        self._package_manager.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        # Filled before it is connected: adding the first entry to an empty combo
        # emits ``currentIndexChanged``, and handling it here would validate a form
        # whose other fields are still empty.
        for choice in PACKAGE_MANAGER_CHOICES:
            self._package_manager.addItem(self._package_manager_label(choice), choice)
        self._package_manager.currentIndexChanged.connect(self._on_package_manager_changed)
        behaviour_layout.addRow(
            self._label(TranslationKey.SETTINGS_PACKAGE_MANAGER),
            self._package_manager_field(),
        )

        layout.addRow(QWidget(self._body))

        # A settings combo commits a choice - a manager pick opens its
        # install question, a language pick retranslates the whole window -
        # so a scroll that merely drifted over the row must not commit
        # anything. The wheel is ignored while the combo's list is closed;
        # clicking first stays the only way to choose.
        self._wheel_guarded = {
            self._theme_combo,
            self._language_combo,
            self._package_manager,
        }
        for combo in self._wheel_guarded:
            combo.installEventFilter(self)

        # The spin boxes' numbers have to start clear of their buttons, not
        # only after the first language switch.
        self._align_spin_boxes()

    # -- construction helpers ---------------------------------------------------

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt override
        """A settings combo does not commit on a scroll nobody asked for.

        Qt's combos change their selection on a wheel event by default, so
        a hover that drifted across the manager row could silently flip the
        tool - and a manager flip opens install questions and re-runs
        probes. The wheel is swallowed while the combo's list is closed;
        once the list is open its own scrolling is left alone, and clicking
        first remains the only way to choose.
        """
        if (
            watched in self._wheel_guarded
            and event.type() == QEvent.Type.Wheel
            and not watched.view().isVisible()
        ):
            return True
        return super().eventFilter(watched, event)

    def _package_manager_field(self) -> QWidget:
        """The package-manager choice, together with the sentence explaining it.

        The hint for this setting has been in both translation tables since the
        page was written and was never rendered, which left the row as a bare
        control: a combo saying "auto" with nothing anywhere saying what
        automatic means. It now travels with the control, and the control is a
        full-width field instead of a sliver sized to its shortest entry.
        """
        container = QWidget(self._body)
        field = QVBoxLayout(container)
        field.setContentsMargins(0, 0, 0, 0)
        field.setSpacing(4)
        field.addWidget(self._package_manager)

        # The manager row's progress bar: small, quiet, and only there while
        # the row has something to report. An install the user asked for
        # here fills it with the percentages the run's own output carries;
        # a failure turns the whole bar red, so the answer to "what
        # happened to my install?" is on the page that started it - while
        # the log below holds the lines and History holds the record.
        self._manager_progress = QProgressBar(container)
        self._manager_progress.setObjectName("managerProgressBar")
        self._manager_progress.setTextVisible(False)
        self._manager_progress.setFixedHeight(6)
        self._manager_progress.setVisible(False)
        field.addWidget(self._manager_progress)

        # Real air between the control and its sentence. The layout's 4px
        # is all that separates the combo from the hint below it once the
        # bar hides - and a focused combo paints its accent border at that
        # very edge, so at high DPI the first description line reads as
        # drawn through the box itself, which is exactly the reported
        # "the description goes into the selection box".
        field.addSpacing(10)

        self._package_manager_hint = QLabel(container)
        self._package_manager_hint.setObjectName("settingsHint")
        self._package_manager_hint.setWordWrap(True)
        self._package_manager_hint.setText(
            self._shell.text(TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT)
        )
        field.addWidget(self._package_manager_hint)
        # Registered under its key, so the language switch re-renders it with the
        # rest of the page rather than leaving one English sentence behind.
        self._labels[TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT] = self._package_manager_hint

        # The live answer to "is my choice installed?" - rendered for the
        # current choice, hidden for ``auto`` (which names no single tool).
        self._package_manager_status = QLabel(container)
        self._package_manager_status.setObjectName("settingsHint")
        self._package_manager_status.setWordWrap(True)
        self._package_manager_status.setVisible(False)
        field.addWidget(self._package_manager_status)

        # Offered only for an installed manager pip can remove: the button's
        # whole promise is "take back out what pip put in", so it appears
        # only where that is exactly true.
        self._manager_remove_button = QPushButton(container)
        self._manager_remove_button.setObjectName("secondaryText")
        self._manager_remove_button.setAutoDefault(False)
        self._manager_remove_button.setVisible(False)
        self._manager_remove_button.clicked.connect(self._remove_manager_clicked)
        field.addWidget(self._manager_remove_button)
        return container

    def _remove_manager_clicked(self) -> None:
        """Route the removal to the confirm-and-run path, by name."""
        choice = str(self._package_manager.currentData() or "auto")
        self.manager_remove_requested.emit(choice)

    def _group(self, layout: QFormLayout, key: TranslationKey) -> QFormLayout:
        """Add a titled group box for *key* and return its form layout.

        The insets live here, in the layout's own contents margins, and not
        in a stylesheet ``padding``: Qt's group-box box model lets the
        stylesheet padding fight - and silently win over - the margins a
        layout sets, which is how the box ended up hugging its last row so
        tightly that the description text read as sunk into the border. The
        generous bottom inset is the fix for that: the last line clears the
        frame at every scale, and the wider spacing gives the rows room to
        be a panel rather than a list.
        """
        box = QGroupBox(self._body)
        box.setObjectName("settingsGroup")
        box.setTitle(self._shell.text(key))
        inner = QFormLayout(box)
        inner.setContentsMargins(20, 26, 20, 22)
        inner.setVerticalSpacing(12)
        # The group spans the whole row, not the form's field column: a box
        # sized by its contents' own widths came out ragged - one group as
        # narrow as its shortest row, another as wide as its longest - and
        # every one of them too small for the settings it held.
        layout.addRow(box)
        self._groups[key] = box
        return inner

    def _line_edit(self, layout: QFormLayout, key: TranslationKey) -> QLineEdit:
        """Add a labelled text field, registered for re-translation."""
        edit = QLineEdit(self._body)
        edit.setObjectName("settingsLineEdit")
        edit.setMinimumWidth(SETTINGS_FIELD_MIN_WIDTH)
        edit.setPlaceholderText(self._shell.text(TranslationKey.SETTINGS_LINES_HINT))
        layout.addRow(self._label(key), edit)
        self._line_edits.append(edit)
        return edit

    def _label(self, key: TranslationKey) -> QLabel:
        label = QLabel(self._body)
        label.setObjectName("settingsLabel")
        label.setText(self._shell.text(key))
        label.setWordWrap(True)
        self._labels[key] = label
        return label

    def _text(self, key: TranslationKey) -> str:
        return self._shell.text(key)

    @staticmethod
    def _theme_label(member: ColorTheme) -> str:
        return {
            ColorTheme.SYSTEM: "system",
            ColorTheme.LIGHT: "light",
            ColorTheme.DARK: "dark",
        }[member]

    @staticmethod
    def _language_label(member: Language) -> str:
        return {
            Language.SYSTEM: "system",
            Language.EN: "en",
            Language.FA: "\u0641\u0627",
        }[member]

    def _package_manager_label(self, choice: str) -> str:
        """What one package-manager entry says in this language.

        Only ``auto`` needs a sentence: every other entry is a product name
        (pip, uv, conda, mamba, poetry, pipx, pipenv, pdm) and translating it
        would make the entry harder to recognise, not easier.
        """
        if choice == "auto":
            return self._shell.text(TranslationKey.SETTINGS_PACKAGE_MANAGER_AUTO)
        return choice

    # -- population -------------------------------------------------------------

    def load_settings(self, settings: AppSettings) -> None:
        """Show *settings* in the controls the page owns."""
        self._loading = True
        try:
            self._theme_combo.setCurrentIndex(list(ColorTheme).index(settings.theme))
            self._language_combo.setCurrentIndex(list(Language).index(settings.language))
            self._page_size.setValue(settings.package_list_page_size)
            self._sidebar_width.setValue(settings.sidebar_width)
            self._index_url.setText(settings.pypi_index_url)
            self._extra_index_urls.setText("\n".join(settings.extra_index_urls))
            self._trusted_hosts.setText("\n".join(settings.trusted_hosts))
            self._confirm_install.setChecked(settings.confirm_before_install)
            self._confirm_uninstall.setChecked(settings.confirm_before_uninstall)
            self._confirm_upgrade.setChecked(settings.confirm_before_upgrade)
            self._show_sizes.setChecked(settings.show_sizes_by_default)
            self._show_outdated.setChecked(settings.show_outdated_by_default)
            self._package_manager.setCurrentIndex(
                PACKAGE_MANAGER_CHOICES.index(settings.package_manager)
            )
        finally:
            self._loading = False
        # The restored choice is reported, never questioned: the honest
        # state belongs on the page from the moment it appears, and the
        # "install it now?" prompt is a selection's question - the report
        # behind the guard was the question greeting the user before the
        # window did.
        self._render_manager_status(settings.package_manager)

    def current_settings(self) -> AppSettings:
        """Return the settings the controls currently describe, validated."""
        return validate_settings(
            AppSettings(
                theme=list(ColorTheme)[self._theme_combo.currentIndex()],
                language=list(Language)[self._language_combo.currentIndex()],
                package_list_page_size=self._page_size.value(),
                sidebar_width=self._sidebar_width.value(),
                pypi_index_url=self._index_url.text().strip(),
                extra_index_urls=_lines(self._extra_index_urls.text()),
                trusted_hosts=_lines(self._trusted_hosts.text()),
                confirm_before_install=self._confirm_install.isChecked(),
                confirm_before_uninstall=self._confirm_uninstall.isChecked(),
                confirm_before_upgrade=self._confirm_upgrade.isChecked(),
                show_sizes_by_default=self._show_sizes.isChecked(),
                show_outdated_by_default=self._show_outdated.isChecked(),
                package_manager=self._package_manager.currentData(),
                # Not edited on this page, but it is a *setting*: rebuilding
                # the whole object from the controls alone would reset it on
                # every save, silently forgetting every folder the user
                # registered on the environments page.
                registered_environments=self._shell.settings.registered_environments,
            )
        )

    # -- change handlers --------------------------------------------------------

    def _on_theme_changed(self, _index: int) -> None:
        """The user picked a theme; validate and push it back through the shell."""
        self.apply_current_settings()

    def _on_language_changed(self, _index: int) -> None:
        """The user picked a language; validate and push it back through the shell."""
        self.apply_current_settings()

    def _on_package_manager_changed(self, _index: int) -> None:
        """The user picked a package manager; persist, rebuild, and tell them
        what they just picked.

        The ``_loading`` guard is the whole difference between a selection
        and a restore: ``load_settings`` sets the combo to the stored
        choice, and a restored choice must never ask its question - the
        report was the question appearing before the window did, because
        the handler guarded its save but not its prompt.
        """
        if self._loading:
            return
        choice = str(self._package_manager.currentData() or "auto")
        # A new selection is a new subject: whatever the last run left on
        # the row's bar - a red failure, a stalled percentage - belongs to
        # the choice the user just left, not to this one.
        self._reset_manager_progress()
        self.apply_current_settings()
        self._update_manager_status(choice)

    # -- the manager row's run ------------------------------------------

    def _reset_manager_progress(self) -> None:
        """Take the row's bar back to its resting state: hidden, not red."""
        bar = self._manager_progress
        bar.setProperty("error", False)
        bar.setVisible(False)
        _repolish(bar)

    def show_manager_run(self, determinate: bool) -> None:
        """Reveal the row's bar for a run the user started from this page.

        ``determinate`` is the run's own answer to whether its output can
        be counted: an install's download and install lines carry
        percentages, so the bar fills with the run's real progress; a
        removal prints nothing countable, so the bar animates instead of
        pretending to a percentage that will never arrive.
        """
        bar = self._manager_progress
        bar.setProperty("error", False)
        bar.setRange(0, 100 if determinate else 0)
        bar.setValue(0)
        bar.setVisible(True)
        _repolish(bar)

    def set_manager_progress(self, percent: int) -> None:
        """Move the row's bar to the percentage the run itself reported."""
        self._manager_progress.setValue(percent)

    def finish_manager_run(self, outcome: RunOutcome) -> None:
        """End the row's bar the way the run actually ended.

        A failure turns the whole bar red and leaves it there - the row's
        word for "ask the log and History why" - and anything else (done,
        or stopped by the user) simply puts the bar away. Either way the
        one-line state underneath is re-probed: an install that succeeded
        has changed the answer, and a red bar over a freshly honest
        status is exactly the report the row exists to give.
        """
        bar = self._manager_progress
        if outcome is RunOutcome.FAILED:
            bar.setProperty("error", True)
            _repolish(bar)
        else:
            self._reset_manager_progress()
        self._render_manager_status(str(self._package_manager.currentData() or "auto"))

    def _update_manager_status(self, choice: str) -> None:
        """Render the chosen manager's state, and ask the question a missing
        one deserves.

        Called for user selections only: the check happens the moment the
        user commits to a tool, so an uninstalled one is met here - with the
        offer to fix it - rather than at the next upgrade. The availability
        probe is resolution only - a PATH walk, no process spawned - which
        is why asking here does not need a worker thread.
        """
        self._render_manager_status(choice)

        if choice == "auto" or manager_available(choice):
            return
        if not manager_installable(choice):
            # A question this application cannot act on is not asked: the
            # row already says where a non-pip manager comes from, and a
            # "yes" that could only be answered with a refusal wastes the
            # click it takes - the reported dead-end of "I said install
            # and nothing happened".
            return

        answer = QMessageBox.question(
            self,
            self._shell.text(TranslationKey.CONFIRM_BOOTSTRAP_TITLE, backend=choice),
            self._shell.text(TranslationKey.SETTINGS_MANAGER_INSTALL_QUESTION, backend=choice),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            # ``==`` and not ``is``: PySide6's static ``QMessageBox.question``
            # answers with the button's *value* (a plain int), not the enum
            # member, so an identity comparison silently ignored every real
            # "Yes" ever clicked - the reported dead-end of "I said install
            # and nothing happened". The tests mock the question with the
            # int form for exactly this reason.
            self.manager_install_requested.emit(choice)

    def _render_manager_status(self, choice: str) -> None:
        """Show the honest one-line state of the chosen manager - no question.

        This is the restore path: ``load_settings`` and the language switch
        call it, because a page should say what is installed the moment it
        appears, and only a *selection* may ask whether to install more.

        The remove button follows the same answer: it appears only for an
        installed manager pip can remove, because its whole promise is
        "take back out what pip put in".
        """
        status = self._package_manager_status
        if choice == "auto":
            status.setVisible(False)
            self._manager_remove_button.setVisible(False)
            return

        installed = manager_available(choice)
        if installed:
            key = TranslationKey.SETTINGS_MANAGER_STATUS_INSTALLED
        elif not manager_installable(choice):
            # The honest state of a manager this application cannot
            # install: where it comes from, instead of a "missing" that
            # invites an install nothing can perform.
            key = TranslationKey.SETTINGS_MANAGER_NOT_INSTALLABLE
        else:
            key = TranslationKey.SETTINGS_MANAGER_STATUS_MISSING
        status.setText(self._shell.text(key, backend=choice))
        status.setVisible(True)

        removable = installed and manager_removable(choice)
        self._manager_remove_button.setText(
            self._shell.text(TranslationKey.SETTINGS_MANAGER_REMOVE, backend=choice)
        )
        self._manager_remove_button.setVisible(removable)

    def apply_current_settings(self) -> None:
        """Validate the controls' current values and persist them."""
        if self._loading:
            return
        settings = self.current_settings()
        old_settings = self._shell.settings
        old_package_manager = old_settings.package_manager
        self._shell.apply_settings(settings)
        # Changing the package manager must reach the services the views share.
        # The window hands this page its context for exactly that: the shell is
        # settings, theme and language state, and has no context of its own, so
        # asking it for one would silently skip the change - which is how picking
        # a manager here used to do nothing until the next launch.
        if settings.package_manager != old_package_manager and self._context is not None:
            self._context.set_package_manager(settings.package_manager)
        # The index settings reach the services the same way, and only when
        # they actually changed: rebuilding them on every save would throw
        # away the version cache because a checkbox moved.
        if self._context is not None and (
            settings.pypi_index_url,
            settings.extra_index_urls,
            settings.trusted_hosts,
            settings.pypi_cache_ttl,
            settings.pypi_cache_max_versions,
        ) != (
            old_settings.pypi_index_url,
            old_settings.extra_index_urls,
            old_settings.trusted_hosts,
            old_settings.pypi_cache_ttl,
            old_settings.pypi_cache_max_versions,
        ):
            self._context.set_index(
                index_url=settings.pypi_index_url,
                extra_index_urls=tuple(settings.extra_index_urls),
                trusted_hosts=tuple(settings.trusted_hosts),
                cache_ttl=settings.pypi_cache_ttl,
                cache_max_versions=settings.pypi_cache_max_versions,
            )
        # The shell re-translates and re-themes the window, which reads the
        # controls back in the new language, so this write does not fight it.
        self._shell.save()
        self.load_settings(settings)

    # -- re-translation ---------------------------------------------------------

    def retranslate(self) -> None:
        """Re-render every string the page owns in the current language."""
        for key, box in self._groups.items():
            box.setTitle(self._shell.text(key))
        for key, label in self._labels.items():
            label.setText(self._shell.text(key))

        self._page_size.setSuffix(
            " " + self._shell.text(TranslationKey.SETTINGS_PACKAGES_PER_PAGE_UNITS)
        )

        for edit in self._line_edits:
            edit.setPlaceholderText(self._shell.text(TranslationKey.SETTINGS_LINES_HINT))

        for index, theme_member in enumerate(ColorTheme):
            self._theme_combo.setItemText(index, self._theme_label(theme_member))
        for index, language_member in enumerate(Language):
            self._language_combo.setItemText(index, self._language_label(language_member))

        for index, choice in enumerate(PACKAGE_MANAGER_CHOICES):
            self._package_manager.setItemText(index, self._package_manager_label(choice))
        self._package_manager.setToolTip(
            self._shell.text(TranslationKey.SETTINGS_PACKAGE_MANAGER_HINT)
        )

        # The manager status line follows the language for the *current*
        # choice, without re-asking its question: a language switch is not
        # a selection, and the answer that was given stands.
        current = str(self._package_manager.currentData() or "auto")
        self._render_manager_status(current)
        self._align_spin_boxes()

    def _align_spin_boxes(self) -> None:
        """Keep the spin boxes' numbers clear of their buttons.

        Qt draws a spin box's text left-aligned whatever the layout
        direction is, but it mirrors the up/down buttons to the left in a
        right-to-left layout - so in Persian the number was painted
        underneath the buttons and its first digit disappeared behind
        them. Aligning the text with the far edge puts it away from the
        buttons in both directions: the buttons sit on the right in a
        left-to-right layout and on the left in a right-to-left one.
        """
        alignment = (
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            if self._shell.is_rtl
            else Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        for spin in (self._page_size, self._sidebar_width):
            spin.setAlignment(alignment)


def _lines(text: str) -> tuple[str, ...]:
    """Split a multi-line field into the non-empty, stripped entries."""
    return tuple(line.strip() for line in text.splitlines() if line.strip())
