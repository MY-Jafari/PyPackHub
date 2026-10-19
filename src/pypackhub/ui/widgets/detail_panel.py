"""The package detail panel.

SPEC section 5 asks for a side panel with the description, links, license,
author, location and the dependency relationships of the selected package. The
panel renders metadata, descriptions and URLs as **plain text** (SPEC section
11.7): nothing a package publishes is interpreted as markup, and a link becomes
an action only when the user clicks it, over HTTPS.

Dependency data comes from :mod:`pypackhub.core.metadata`, which reads it from
the installed distribution rather than from the network, so the panel works
offline and describes what is actually installed.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pypackhub.core.inventory import PackageRow, PackageStatus
from pypackhub.core.metadata import PackageMetadata
from pypackhub.ui.formatting import UNKNOWN_TEXT, format_bytes
from pypackhub.ui.i18n.translations import TranslationKey
from pypackhub.ui.i18n.translator import Translator
from pypackhub.ui.links import open_external_url, safe_url

__all__ = ["MAX_LISTED_RELATIONSHIPS", "DetailPanel"]

#: How many requirements or dependents the panel lists before summarising the
#: rest. A package with 200 requirements must not make the panel 200 rows tall.
MAX_LISTED_RELATIONSHIPS = 12

#: The scalar fields, in the order they are shown.
_FIELD_KEYS: tuple[tuple[str, TranslationKey], ...] = (
    ("status", TranslationKey.PACKAGES_STATUS),
    ("size", TranslationKey.PACKAGES_SIZE),
    ("license", TranslationKey.DETAIL_LICENSE),
    ("author", TranslationKey.DETAIL_AUTHOR),
    ("python", TranslationKey.DETAIL_PYTHON),
    ("location", TranslationKey.DETAIL_LOCATION),
)

_STATUS_KEYS: dict[PackageStatus, TranslationKey] = {
    PackageStatus.UP_TO_DATE: TranslationKey.STATUS_UP_TO_DATE,
    PackageStatus.OUTDATED: TranslationKey.STATUS_OUTDATED,
    PackageStatus.UNKNOWN: TranslationKey.STATUS_UNKNOWN,
}


class DetailPanel(QWidget):
    """The side panel that describes one selected package."""

    link_opened = Signal(str)

    def __init__(self, *, translator: Translator, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._translator = translator
        self._row: PackageRow | None = None
        self._metadata: PackageMetadata | None = None
        self._dependents: tuple[str, ...] = ()

        self.setObjectName("detailPanel")
        self.setMinimumWidth(280)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(10)

        self.title_label = QLabel(self)
        self.title_label.setObjectName("pageTitle")
        self.title_label.setWordWrap(True)
        outer.addWidget(self.title_label)

        self.version_label = QLabel(self)
        self.version_label.setObjectName("versionLabel")
        self.version_label.setWordWrap(True)
        outer.addWidget(self.version_label)

        self.summary_label = QLabel(self)
        self.summary_label.setObjectName("secondaryText")
        self.summary_label.setWordWrap(True)
        outer.addWidget(self.summary_label)

        self.notice_label = QLabel(self)
        self.notice_label.setObjectName("safetyNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.setVisible(False)
        outer.addWidget(self.notice_label)

        # Everything below the header scrolls, so a long requirement list never
        # stretches the window past the screen.
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_body = QWidget(self._scroll)
        self._scroll.setWidget(self._scroll_body)
        outer.addWidget(self._scroll, 1)

        body = QVBoxLayout(self._scroll_body)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(12)

        self._form = QFormLayout()
        # AlignLeading keeps the labels on the reading edge of whichever
        # language is active: left in English, right in Persian.
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignLeading)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        body.addLayout(self._form)

        self.fields: dict[str, QLabel] = {}
        self.field_labels: dict[str, QLabel] = {}
        for key, translation_key in _FIELD_KEYS:
            value_label = QLabel(UNKNOWN_TEXT, self._scroll_body)
            value_label.setWordWrap(True)
            value_label.setObjectName("secondaryText")
            name_label = QLabel(translator.translate(translation_key), self._scroll_body)
            name_label.setObjectName("fieldLabel")
            self.fields[key] = value_label
            self.field_labels[key] = name_label
            self._form.addRow(name_label, value_label)

        self.requires_heading = self._add_heading(body, TranslationKey.DETAIL_REQUIRES)
        self.requires_label = self._add_body_label(body)
        self.required_by_heading = self._add_heading(body, TranslationKey.DETAIL_REQUIRED_BY)
        self.required_by_label = self._add_body_label(body)

        self.links_heading = self._add_heading(body, TranslationKey.DETAIL_LINKS)
        self.links_container = QWidget(self._scroll_body)
        self._links_layout = QVBoxLayout(self.links_container)
        self._links_layout.setContentsMargins(0, 0, 0, 0)
        self._links_layout.setSpacing(4)
        body.addWidget(self.links_container)
        body.addStretch(1)

        self._link_buttons: list[QPushButton] = []

        self.clear()
        self.retranslate(translator)

    # -- public API ----------------------------------------------------

    @property
    def row(self) -> PackageRow | None:
        return self._row

    @property
    def requires_text(self) -> str:
        return self.requires_label.text()

    @property
    def required_by_text(self) -> str:
        return self.required_by_label.text()

    @property
    def link_urls(self) -> tuple[str, ...]:
        return tuple(button.toolTip() for button in self._link_buttons)

    def show_row(
        self,
        row: PackageRow | None,
        *,
        metadata: PackageMetadata | None = None,
        dependents: tuple[str, ...] = (),
    ) -> None:
        """Render one package, with whatever installed metadata is available."""
        self._row = row
        self._metadata = metadata
        self._dependents = dependents

        if row is None:
            self.clear()
            return

        self.title_label.setText(row.name)
        self.version_label.setText(self._version_text(row))

        summary = row.summary or (metadata.summary if metadata else "")
        self.summary_label.setText(summary)
        self.summary_label.setVisible(bool(summary))

        # same row still visible: refresh the panel with the (possibly newer)
        # installed version and metadata, without re-rendering everything.
        self._refresh_from_row(row, metadata=metadata)

        self._render_notice(row)
        self._render_fields(row)
        self.requires_label.setText(self._list_text(metadata.requires if metadata else ()))
        self.required_by_label.setText(self._list_text(dependents))
        self._render_links(row)

    def clear(self) -> None:
        """Go back to the empty state."""
        self._row = None
        self._metadata = None
        self._dependents = ()
        self.title_label.setText(self._translator.translate(TranslationKey.DETAIL_TITLE))
        self.version_label.setText(self._translator.translate(TranslationKey.DETAIL_NO_SELECTION))
        self.summary_label.clear()
        self.summary_label.setVisible(False)
        self.notice_label.clear()
        self.notice_label.setVisible(False)
        self.requires_label.setText(self._translator.translate(TranslationKey.DETAIL_NONE))
        self.required_by_label.setText(self._translator.translate(TranslationKey.DETAIL_NONE))
        for label in self.fields.values():
            label.setText(UNKNOWN_TEXT)
        self._clear_links()

    def _refresh_from_row(self, row: PackageRow, *, metadata: PackageMetadata | None) -> None:
        """Refresh the view using the *latest* row/row-version, not the last snapshot."""
        self._row = row
        self._metadata = metadata
        self.title_label.setText(row.name)
        self.version_label.setText(self._version_text(row))
        summary = row.summary or (metadata.summary if metadata else "")
        self.summary_label.setText(summary)
        self.summary_label.setVisible(bool(summary))
        self._render_notice(row)
        self._render_fields(row)
        self.requires_label.setText(self._list_text(metadata.requires if metadata else ()))
        self.required_by_label.setText(self._list_text(self._dependents))
        self._render_links(row)

    def _version_text(self, row: PackageRow) -> str:
        """Return the installed-version line shown in the panel."""
        if row.latest and row.is_outdated:
            return f"{row.installed} \u2192 {row.latest}"
        return row.installed or UNKNOWN_TEXT

    def _render_notice(self, row: PackageRow) -> None:
        """Flags that change how a user should read the row."""
        notices: list[str] = []
        if row.editable:
            notices.append(self._translator.translate(TranslationKey.DETAIL_EDITABLE))
        if row.direct_reference:
            notices.append(self._translator.translate(TranslationKey.DETAIL_DIRECT_REFERENCE))
        if row.error:
            notices.append(row.error)

        self.notice_label.setText("\n".join(notices))
        self.notice_label.setVisible(bool(notices))

    def _render_fields(self, row: PackageRow) -> None:
        metadata = self._metadata
        values = {
            "status": self._translator.translate(_STATUS_KEYS[row.status]),
            "size": format_bytes(row.size_bytes),
            "license": (row.license or (metadata.license if metadata else "")).strip(),
            "author": metadata.author.strip() if metadata else "",
            "python": (
                row.requires_python or (metadata.requires_python if metadata else "")
            ).strip(),
            "location": str(row.location) if row.location else "",
        }

        for key, value in values.items():
            self.fields[key].setText(value or UNKNOWN_TEXT)

    def _add_heading(self, layout: QVBoxLayout, key: TranslationKey) -> QLabel:
        label = QLabel(self._translator.translate(key), self._scroll_body)
        label.setObjectName("detailHeading")
        layout.addWidget(label)
        return label

    def _add_body_label(self, layout: QVBoxLayout) -> QLabel:
        label = QLabel(self._scroll_body)
        label.setWordWrap(True)
        label.setObjectName("secondaryText")
        layout.addWidget(label)
        return label

    def _list_text(self, values: tuple[str, ...]) -> str:
        if not values:
            return self._translator.translate(TranslationKey.DETAIL_NONE)

        shown = values[:MAX_LISTED_RELATIONSHIPS]
        text = "\n".join(f"\u2022 {value}" for value in shown)
        if len(values) > len(shown):
            text += "\n" + self._translator.translate(
                TranslationKey.DETAIL_MORE, count=len(values) - len(shown)
            )
        return text

    def _render_links(self, row: PackageRow) -> None:
        self._clear_links()
        self._add_link(TranslationKey.DETAIL_HOME_PAGE, row.home_page)
        self._add_link(TranslationKey.DETAIL_PROJECT_URL, row.project_url)
        self.links_container.setVisible(bool(self._link_buttons))

    def _add_link(self, key: TranslationKey, url: str) -> None:
        checked = safe_url(url)
        if not checked:
            return

        button = QPushButton(self._translator.translate(key), self.links_container)
        button.setObjectName("ghostButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(checked)
        button.clicked.connect(lambda _checked=False, target=checked: self._open(target))
        self._links_layout.addWidget(button)
        self._link_buttons.append(button)

    def _open(self, url: str) -> None:
        if open_external_url(url):
            self.link_opened.emit(url)

    def _clear_links(self) -> None:
        for button in self._link_buttons:
            self._links_layout.removeWidget(button)
            button.setParent(None)
            button.deleteLater()
        self._link_buttons.clear()
        self.links_container.setVisible(False)

    def retranslate(self, translator: Translator) -> None:
        """Re-render the chrome after a language switch, then the content."""
        self._translator = translator

        for key, translation_key in _FIELD_KEYS:
            self.field_labels[key].setText(translator.translate(translation_key))

        self.requires_heading.setText(translator.translate(TranslationKey.DETAIL_REQUIRES))
        self.required_by_heading.setText(translator.translate(TranslationKey.DETAIL_REQUIRED_BY))
        self.links_heading.setText(translator.translate(TranslationKey.DETAIL_LINKS))

        if self._row is not None:
            self.show_row(self._row, metadata=self._metadata, dependents=self._dependents)
        else:
            self.clear()
