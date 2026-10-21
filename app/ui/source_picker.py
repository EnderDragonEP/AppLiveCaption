"""The audio source button and its flyout (Microphone / System audio / Apps)."""

from __future__ import annotations

import copy
import sys

from PySide6.QtCore import QFileInfo, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFileIconProvider, QHBoxLayout, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    Flyout,
    FlyoutViewBase,
    FluentIcon,
    IconWidget,
    PushButton,
    ScrollArea,
    SegmentedWidget,
    StrongBodyLabel,
    ToolTipFilter,
    TransparentToolButton,
)

from ..audio.app_list import AppEntry, list_apps
from ..audio.capture import MODE_APPS, MODE_MIC, MODE_SYSTEM, CaptureSelection
from ..audio.devices import DEFAULT_DEVICE, list_endpoints, strip_loopback_suffix
from ..audio.process_loopback import process_loopback_supported
from ..audio.wasapi_com import PROCESS_LOOPBACK_MIN_BUILD
from ..i18n import tr
from .async_utils import run_async

_icon_provider: QFileIconProvider | None = None
_icon_cache: dict[str, QIcon] = {}


def app_icon(exe_path: str) -> QIcon:
    global _icon_provider
    if not exe_path:
        return FluentIcon.APPLICATION.icon()
    icon = _icon_cache.get(exe_path)
    if icon is None:
        if _icon_provider is None:
            _icon_provider = QFileIconProvider()
        icon = _icon_provider.icon(QFileInfo(exe_path))
        if icon.isNull():
            icon = FluentIcon.APPLICATION.icon()
        _icon_cache[exe_path] = icon
    return icon


def short_device(name: str) -> str:
    """'Microphone (2- HyperX SoloCast)' -> 'HyperX SoloCast'."""
    name = strip_loopback_suffix(name)
    if "(" in name and name.endswith(")"):
        inner = name[name.index("(") + 1:-1]
        if inner[:3].strip().rstrip("-").isdigit():
            inner = inner.split("-", 1)[-1].strip()
        return inner or name
    return name


def selection_summary(sel: CaptureSelection) -> str:
    """Button label, e.g. 'Chrome + Teams' or 'System audio + Mic'."""
    mic = tr("Mic")
    if sel.mode == MODE_MIC:
        return tr("Microphone") if not sel.mic_device else f"{tr('Mic')}: {short_device(sel.mic_device)}"
    if sel.mode == MODE_SYSTEM:
        base = tr("System audio") if not sel.system_device else short_device(sel.system_device)
        return f"{base} + {mic}" if sel.include_mic else base
    names = [sel.app_names.get(a) or a.removesuffix(".exe") for a in sel.apps]
    if not names:
        label = tr("No apps selected")
    elif len(names) <= 3:
        label = " + ".join(names)
    else:
        label = " + ".join(names[:2]) + " " + tr("+ {n} more").format(n=len(names) - 2)
    return f"{label} + {mic}" if sel.include_mic and names else label


def selection_icon(sel: CaptureSelection):
    return {MODE_MIC: FluentIcon.MICROPHONE, MODE_SYSTEM: FluentIcon.SPEAKERS}.get(sel.mode, FluentIcon.APPLICATION)


class _AppRow(QWidget):
    toggled = Signal(str, bool)

    def __init__(self, entry: AppEntry | None, key: str, name: str, checked: bool, parent=None):
        super().__init__(parent)
        self.key = key
        lay = QHBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.setSpacing(8)
        self.check = CheckBox(self)
        self.check.setFixedWidth(28)
        self.check.setChecked(checked)
        self.check.stateChanged.connect(lambda _s: self.toggled.emit(self.key, self.check.isChecked()))
        icon = IconWidget(app_icon(entry.exe_path if entry else ""), self)
        icon.setFixedSize(20, 20)
        label = BodyLabel(name, self)
        label.setToolTip(key)
        lay.addWidget(self.check)
        lay.addWidget(icon)
        lay.addWidget(label, 1)
        if entry is None:
            status = tr("Not running")
        elif entry.playing:
            status = tr("Playing")
        else:
            status = ""
        if status:
            lay.addWidget(CaptionLabel(status, self))

    def mousePressEvent(self, e):  # noqa: N802 - whole row toggles the checkbox
        self.check.setChecked(not self.check.isChecked())
        super().mousePressEvent(e)


class SourcePickerView(FlyoutViewBase):
    selectionChanged = Signal(object)

    def __init__(self, selection: CaptureSelection, parent=None):
        super().__init__(parent)
        self.sel = copy.deepcopy(selection)
        self._apps: list[AppEntry] = []
        self._loading = False
        self.setFixedWidth(380)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)
        root.addWidget(StrongBodyLabel(self.tr("Audio source"), self))

        self.segment = SegmentedWidget(self)
        self.segment.addItem(MODE_MIC, self.tr("Microphone"), lambda: self._set_mode(MODE_MIC))
        self.segment.addItem(MODE_SYSTEM, self.tr("System audio"), lambda: self._set_mode(MODE_SYSTEM))
        self.per_app = process_loopback_supported()
        if self.per_app:
            self.segment.addItem(MODE_APPS, self.tr("Apps"), lambda: self._set_mode(MODE_APPS))
        root.addWidget(self.segment)
        if not self.per_app:
            note = CaptionLabel(self.tr(
                "Captioning individual apps needs Windows 11 (Windows build {build} or later). "
                "This PC runs build {current}.").format(build=PROCESS_LOOPBACK_MIN_BUILD,
                                                        current=sys.getwindowsversion().build), self)
            note.setWordWrap(True)
            root.addWidget(note)
            if self.sel.mode == MODE_APPS:
                self.sel.mode = MODE_SYSTEM

        self.stack = QStackedWidget(self)
        root.addWidget(self.stack)
        endpoints = list_endpoints()
        self.stack.addWidget(self._mic_page(endpoints))
        self.stack.addWidget(self._system_page(endpoints))
        self.stack.addWidget(self._apps_page(endpoints))

        self.timer = QTimer(self)
        self.timer.setInterval(3000)
        self.timer.timeout.connect(self.refresh_apps)

        self.segment.setCurrentItem(self.sel.mode)
        self._show_page(self.sel.mode)

    # -- pages --------------------------------------------------------------
    def _device_combo(self, names: list[str], default_name: str | None, current: str, loopback: bool) -> ComboBox:
        combo = ComboBox(self)
        default_label = self.tr("Default") + (f" ({short_device(default_name)})" if default_name else "")
        combo.addItem(default_label, userData=DEFAULT_DEVICE)
        for n in names:
            combo.addItem(n, userData=f"{n} [Loopback]" if loopback else n)
        idx = 0
        for i in range(combo.count()):
            if combo.itemData(i) == current:
                idx = i
        if current and idx == 0:
            # The remembered device is not connected; keep it selectable.
            combo.addItem(self.tr("{name} (not connected)").format(name=strip_loopback_suffix(current)), userData=current)
            idx = combo.count() - 1
        combo.setCurrentIndex(idx)
        return combo

    def _mic_page(self, ep) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(BodyLabel(self.tr("Microphone"), page))
        self.mic_combo = self._device_combo(ep.inputs, ep.default_input, self.sel.mic_device, False)
        self.mic_combo.currentIndexChanged.connect(self._on_mic_device)
        lay.addWidget(self.mic_combo)
        if not ep.inputs:
            lay.addWidget(CaptionLabel(self.tr("No microphone found."), page))
        lay.addStretch(1)
        return page

    def _system_page(self, ep) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(BodyLabel(self.tr("Caption everything playing on"), page))
        self.out_combo = self._device_combo(ep.outputs, ep.default_output, self.sel.system_device, True)
        self.out_combo.currentIndexChanged.connect(self._on_out_device)
        lay.addWidget(self.out_combo)
        self.sys_mic = CheckBox(self.tr("Also caption my microphone"), page)
        self.sys_mic.setChecked(self.sel.include_mic)
        self.sys_mic.stateChanged.connect(lambda _s: self._set_include_mic(self.sys_mic.isChecked()))
        lay.addWidget(self.sys_mic)
        lay.addStretch(1)
        return page

    def _apps_page(self, ep) -> QWidget:
        page = QWidget(self)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        head = QHBoxLayout()
        head.addWidget(BodyLabel(self.tr("Caption only these apps"), page), 1)
        self.refresh_btn = TransparentToolButton(FluentIcon.SYNC, page)
        self.refresh_btn.setToolTip(self.tr("Refresh"))
        self.refresh_btn.installEventFilter(ToolTipFilter(self.refresh_btn))
        self.refresh_btn.clicked.connect(self.refresh_apps)
        head.addWidget(self.refresh_btn)
        lay.addLayout(head)

        self.app_area = ScrollArea(page)
        self.app_area.setWidgetResizable(True)
        self.app_area.setFixedHeight(240)
        self.app_area.setStyleSheet("QScrollArea{background:transparent;border:none}")
        self.app_list = QWidget()
        self.app_list.setStyleSheet("background:transparent")
        self.app_layout = QVBoxLayout(self.app_list)
        self.app_layout.setContentsMargins(0, 0, 8, 0)
        self.app_layout.setSpacing(2)
        self.app_layout.addStretch(1)
        self.app_area.setWidget(self.app_list)
        lay.addWidget(self.app_area)

        self.app_hint = CaptionLabel(self.tr("Loading apps…"), page)
        self.app_hint.setWordWrap(True)
        # Reserve two lines so the flyout doesn't change height when the text does.
        self.app_hint.setMinimumHeight(self.app_hint.fontMetrics().lineSpacing() * 2 + 4)
        self.app_hint.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(self.app_hint)

        mic_row = QHBoxLayout()
        self.app_mic = CheckBox(self.tr("Add microphone"), page)
        self.app_mic.setChecked(self.sel.include_mic)
        self.app_mic.stateChanged.connect(lambda _s: self._set_include_mic(self.app_mic.isChecked()))
        mic_row.addWidget(self.app_mic)
        self.app_mic_combo = self._device_combo(ep.inputs, ep.default_input, self.sel.mic_device, False)
        self.app_mic_combo.currentIndexChanged.connect(self._on_app_mic_device)
        self.app_mic_combo.setEnabled(self.sel.include_mic)
        mic_row.addWidget(self.app_mic_combo, 1)
        lay.addLayout(mic_row)
        return page

    # -- app list -----------------------------------------------------------
    def refresh_apps(self) -> None:
        if self._loading:
            return
        self._loading = True
        run_async(list_apps, self._on_apps, self, on_error=lambda _e: setattr(self, "_loading", False))

    def _on_apps(self, apps: list[AppEntry]) -> None:
        self._loading = False
        self._apps = apps
        for a in apps:
            self.sel.app_names[a.key] = a.name  # refresh names saved by older versions
        self._rebuild_rows()

    def _rebuild_rows(self) -> None:
        while self.app_layout.count() > 1:
            item = self.app_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        running = {a.key: a for a in self._apps}
        rows: list[tuple[AppEntry | None, str, str]] = [(a, a.key, a.name) for a in self._apps]
        # Remembered apps that are not running stay in the list so they can be unticked.
        for key in self.sel.apps:
            if key not in running:
                rows.append((None, key, self.sel.app_names.get(key, key)))
        for entry, key, name in rows:
            row = _AppRow(entry, key, name, key in self.sel.apps, self.app_list)
            row.toggled.connect(self._on_app_toggled)
            self.app_layout.insertWidget(self.app_layout.count() - 1, row)
        if not rows:
            self.app_hint.setText(self.tr("No apps are playing audio or showing a window."))
        else:
            self.app_hint.setText(self.tr("Only the ticked apps are captioned. Apps you tick are remembered "
                                          "and picked up again when they start."))

    def _on_app_toggled(self, key: str, checked: bool) -> None:
        if checked and key not in self.sel.apps:
            self.sel.apps.append(key)
        elif not checked and key in self.sel.apps:
            self.sel.apps.remove(key)
        self._emit()

    # -- state ----------------------------------------------------------------
    def _set_mode(self, mode: str) -> None:
        self.sel.mode = mode
        self._show_page(mode)
        self._emit()

    def _show_page(self, mode: str) -> None:
        index = {MODE_MIC: 0, MODE_SYSTEM: 1, MODE_APPS: 2}[mode]
        # Size the stack to the visible page only, so the flyout shrinks and grows with it.
        for i in range(self.stack.count()):
            policy = QSizePolicy.Policy.Preferred if i == index else QSizePolicy.Policy.Ignored
            self.stack.widget(i).setSizePolicy(policy, policy)
        self.stack.setCurrentIndex(index)
        if mode == MODE_APPS:
            self.refresh_apps()
            self.timer.start()
        else:
            self.timer.stop()
        self.stack.adjustSize()
        self.adjustSize()
        # Until Flyout.make() adopts this view, window() is the main window;
        # resizing that would shrink the whole app to its minimum size.
        flyout = self.window()
        if isinstance(flyout, Flyout):
            flyout.adjustSize()

    def _set_include_mic(self, on: bool) -> None:
        self.sel.include_mic = on
        self.app_mic_combo.setEnabled(on)
        for box in (self.sys_mic, self.app_mic):
            if box.isChecked() != on:
                box.blockSignals(True)
                box.setChecked(on)
                box.blockSignals(False)
        self._emit()

    def _on_mic_device(self, _i: int) -> None:
        self.sel.mic_device = self.mic_combo.currentData() or DEFAULT_DEVICE
        self._emit()

    def _on_app_mic_device(self, _i: int) -> None:
        self.sel.mic_device = self.app_mic_combo.currentData() or DEFAULT_DEVICE
        self._emit()

    def _on_out_device(self, _i: int) -> None:
        self.sel.system_device = self.out_combo.currentData() or DEFAULT_DEVICE
        self._emit()

    def _emit(self) -> None:
        self.selectionChanged.emit(copy.deepcopy(self.sel))

    def hideEvent(self, e):  # noqa: N802
        self.timer.stop()
        super().hideEvent(e)


class SourceButton(PushButton):
    """Shows the current selection; opens the picker flyout when clicked."""

    MAX_WIDTH = 440

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setIconSize(QSize(16, 16))
        self.setMinimumWidth(200)
        self.setMaximumWidth(self.MAX_WIDTH)
        self._full_text = ""

    def set_selection(self, sel: CaptureSelection) -> None:
        self._full_text = selection_summary(sel)
        self.setToolTip(self._full_text)
        self.setIcon(selection_icon(sel))
        self._elide(self.MAX_WIDTH)
        self.updateGeometry()

    def _elide(self, width: int) -> None:
        # Leave room for the icon and the button's padding.
        text = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(60, width - 64))
        if text != self.text():
            self.setText(text)

    def sizeHint(self):  # noqa: N802 - size for the full label, not the elided one
        hint = super().sizeHint()
        full = self.fontMetrics().horizontalAdvance(self._full_text) + 64
        hint.setWidth(max(self.minimumWidth(), min(self.MAX_WIDTH, full)))
        return hint

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._elide(self.width())
