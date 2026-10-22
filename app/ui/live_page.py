"""Live Captions page."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    ComboBox,
    Flyout,
    FlyoutAnimationType,
    FluentIcon,
    IndeterminateProgressRing,
    PrimaryPushButton,
    ProgressBar,
    SimpleCardWidget,
    TitleLabel,
    TogglePushButton,
    ToolTipFilter,
    isDarkTheme,
    qconfig,
)

from ..asr.languages import language_name
from ..audio.capture import CaptureSelection
from ..config import cfg
from ..models.catalog import ModelInfo, get_model, load_catalog
from ..models.storage import is_downloaded
from .caption_model import CaptionModel
from .caption_view import CaptionView
from .model_combo import ModelComboBox
from .source_picker import SourceButton, SourcePickerView


def format_size(mb: float) -> str:
    return f"{mb / 1024:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"


class LivePage(QWidget):
    startRequested = Signal()
    stopRequested = Signal()
    modelRequested = Signal(str)
    languageChanged = Signal(str)
    selectionChanged = Signal(object)
    overlayToggled = Signal(bool)

    def __init__(self, captions: CaptionModel, parent=None):
        super().__init__(parent)
        self.setObjectName("livePage")
        self.captions = captions
        self.selection = CaptureSelection.from_dict(cfg.get(cfg.captureSelection))
        self._model_ids: list[str] = []
        self._languages: list[str] = []
        self._running = False

        root = QVBoxLayout(self)
        root.setContentsMargins(36, 24, 36, 20)
        root.setSpacing(14)
        root.addWidget(TitleLabel(self.tr("Live captions"), self))

        # Row 1: what to caption.
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.start_btn = PrimaryPushButton(FluentIcon.PLAY_SOLID, self.tr("Start"), self)
        self.start_btn.setMinimumWidth(110)
        self.start_btn.clicked.connect(self._on_start_clicked)
        bar.addWidget(self.start_btn)

        self.source_btn = SourceButton(self)
        self.source_btn.set_selection(self.selection)
        self.source_btn.setToolTip(self.tr("Audio source"))
        self.source_btn.clicked.connect(self._open_source_picker)
        bar.addWidget(self.source_btn)
        bar.addStretch(1)

        self.overlay_btn = TogglePushButton(FluentIcon.TRANSPARENT, self.tr("Overlay"), self)
        self.overlay_btn.setToolTip(self.tr("Show captions in a floating window on top of other apps"))
        self.overlay_btn.installEventFilter(ToolTipFilter(self.overlay_btn))
        self.overlay_btn.toggled.connect(self.overlayToggled)
        bar.addWidget(self.overlay_btn)
        root.addLayout(bar)

        # Row 2: how to caption it.
        bar2 = QHBoxLayout()
        bar2.setSpacing(10)
        bar2.addWidget(BodyLabel(self.tr("Model"), self))
        self.model_combo = ModelComboBox(self)
        self.model_combo.setMinimumWidth(280)
        self.model_combo.setToolTip(self.tr("Speech recognition model"))
        self.model_combo.installEventFilter(ToolTipFilter(self.model_combo))
        bar2.addWidget(self.model_combo)
        self.loading_ring = IndeterminateProgressRing(self, start=False)
        self.loading_ring.setFixedSize(22, 22)
        self.loading_ring.setStrokeWidth(3)
        # Keep the spinner's space reserved, so the Language selector doesn't jump
        # sideways (or get pushed over the dropdown) whenever a model loads.
        ring_policy = self.loading_ring.sizePolicy()
        ring_policy.setRetainSizeWhenHidden(True)
        self.loading_ring.setSizePolicy(ring_policy)
        self.loading_ring.hide()
        bar2.addWidget(self.loading_ring)
        bar2.addSpacing(12)
        bar2.addWidget(BodyLabel(self.tr("Language"), self))
        self.lang_combo = ComboBox(self)
        self.lang_combo.setMinimumWidth(200)
        self.lang_combo.setToolTip(self.tr("Spoken language"))
        self.lang_combo.installEventFilter(ToolTipFilter(self.lang_combo))
        bar2.addWidget(self.lang_combo)
        bar2.addStretch(1)
        root.addLayout(bar2)

        self.card = SimpleCardWidget(self)
        card_lay = QVBoxLayout(self.card)
        card_lay.setContentsMargins(0, 0, 0, 0)
        self.view = CaptionView(captions, self.card)
        card_lay.addWidget(self.view)
        root.addWidget(self.card, 1)

        status = QHBoxLayout()
        status.setSpacing(12)
        self.state_label = BodyLabel(self.tr("Stopped"), self)
        status.addWidget(self.state_label)
        self.level_bar = ProgressBar(self, useAni=False)
        self.level_bar.setRange(0, 100)
        self.level_bar.setFixedWidth(90)
        self.level_bar.setToolTip(self.tr("Input level"))
        status.addWidget(self.level_bar)
        self.latency_label = CaptionLabel("", self)
        status.addWidget(self.latency_label)
        status.addStretch(1)
        self.device_label = CaptionLabel("", self)
        status.addWidget(self.device_label)
        root.addLayout(status)

        self.refresh_models()
        self.model_combo.currentIndexChanged.connect(self._on_model_index)
        self.lang_combo.currentIndexChanged.connect(self._on_language_index)
        self.apply_style()
        qconfig.themeChanged.connect(lambda _t: self.apply_style())
        for item in (cfg.captionFont, cfg.captionFontSize, cfg.finalColor,
                     cfg.partialColor):
            item.valueChanged.connect(lambda _v: self.apply_style())
        self.set_running(False)

    # -- appearance ---------------------------------------------------------
    def apply_style(self) -> None:
        font = QFont(cfg.get(cfg.captionFont), cfg.get(cfg.captionFontSize))
        if isDarkTheme():
            final, partial = QColor(240, 240, 240), QColor(150, 150, 150)
        else:
            final, partial = QColor(26, 26, 26), QColor(130, 130, 130)
        # A color still at its default ("Default color" in Settings) follows the theme.
        if QColor(cfg.get(cfg.finalColor)) != QColor(cfg.finalColor.defaultValue):
            final = QColor(cfg.get(cfg.finalColor))
        if QColor(cfg.get(cfg.partialColor)) != QColor(cfg.partialColor.defaultValue):
            partial = QColor(cfg.get(cfg.partialColor))
        self.view.set_style(font, final, partial)

    # -- models -------------------------------------------------------------
    def refresh_models(self) -> None:
        """Rebuild the dropdown (download status may have changed), keeping the selected model."""
        current = self.selected_model_id()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self._model_ids = []
        for m in load_catalog():
            # Models that aren't downloaded are grayed out; picking one offers to download it.
            self.model_combo.addItem(f"{m.name}  ·  {format_size(m.size_mb)}")
            self.model_combo.set_item_dimmed(self.model_combo.count() - 1, not is_downloaded(m))
            self._model_ids.append(m.id)
        self.model_combo.setPlaceholderText(self.tr("Select a model") if self._model_ids else
                                            self.tr("No models: add one on the Models page"))
        # Nothing selected (e.g. the model was unloaded) shows the placeholder.
        self.model_combo.setCurrentIndex(self._model_ids.index(current) if current in self._model_ids else -1)
        self.model_combo.setEnabled(bool(self._model_ids))
        self.model_combo.blockSignals(False)
        if not self._languages:
            # Until a model is loaded (none may be, at start-up), list the languages of the selected
            # model, else of the one used last time, else of the first in the list.
            info = get_model(current or "") or get_model(cfg.get(cfg.modelId)) or next(iter(load_catalog()), None)
            if info:
                self.set_languages(list(info.languages), info)

    def select_model(self, model_id: str | None) -> None:
        """Show ``model_id`` as selected without emitting modelRequested ("Select a model" if it isn't listed)."""
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(self._model_ids.index(model_id) if model_id in self._model_ids else -1)
        self.model_combo.blockSignals(False)

    def selected_model_id(self) -> str | None:
        """The model the dropdown shows; None while it shows "Select a model"."""
        index = self.model_combo.currentIndex()
        return self._model_ids[index] if 0 <= index < len(getattr(self, "_model_ids", ())) else None

    def _on_model_index(self, index: int) -> None:
        if 0 <= index < len(self._model_ids):
            self.modelRequested.emit(self._model_ids[index])

    def set_model_loading(self, loading: bool, info: ModelInfo | None = None) -> None:
        self.loading_ring.setVisible(loading)
        if loading:
            self.loading_ring.start()
            if info:
                self.device_label.setText(self.tr("Loading {model}…").format(model=info.name))
        else:
            self.loading_ring.stop()

    def set_device(self, text: str) -> None:
        self.device_label.setText(text)

    # -- languages -----------------------------------------------------------
    def set_languages(self, codes: list[str], info: ModelInfo | None = None) -> None:
        info = info or get_model(self.selected_model_id() or "")
        detect = info is None or info.auto_detect
        ordered = sorted(codes, key=lambda c: (c not in ("zh", "en"), language_name(c)))
        self._languages = (["auto"] if detect else []) + ordered
        current = cfg.get(cfg.language)
        self.lang_combo.blockSignals(True)
        self.lang_combo.clear()
        for code in self._languages:
            if code == "auto":
                self.lang_combo.addItem(self.tr("Auto-detect"))
            else:
                label = info.language_label(code) if info else language_name(code)
                self.lang_combo.addItem(self.tr(label))
        if current not in self._languages:
            # Without auto-detect the model uses its own language. The saved choice
            # is kept for the next model that can auto-detect.
            current = "auto" if detect else (info.default_language or self._languages[0])
        if self._languages:
            self.lang_combo.setCurrentIndex(self._languages.index(current))
        self.lang_combo.blockSignals(False)
        if detect and cfg.get(cfg.language) != current:
            cfg.set(cfg.language, current)
        # Tell the worker what the dropdown now shows, so the two never disagree.
        self.languageChanged.emit(current)

    def selected_language(self) -> str:
        """The language code shown in the dropdown, or "auto"."""
        index = self.lang_combo.currentIndex()
        return self._languages[index] if 0 <= index < len(self._languages) else cfg.get(cfg.language)

    def _on_language_index(self, index: int) -> None:
        if 0 <= index < len(self._languages):
            code = self._languages[index]
            cfg.set(cfg.language, code)
            self.languageChanged.emit(code)

    def reset_language(self) -> None:
        cfg.set(cfg.language, "auto")
        self.lang_combo.blockSignals(True)
        self.lang_combo.setCurrentIndex(0)
        self.lang_combo.blockSignals(False)

    # -- source ------------------------------------------------------------------
    def _open_source_picker(self) -> None:
        view = SourcePickerView(self.selection, self.window())
        view.selectionChanged.connect(self._on_selection)
        Flyout.make(view, self.source_btn, self.window(), FlyoutAnimationType.DROP_DOWN)

    def _on_selection(self, sel: CaptureSelection) -> None:
        self.selection = sel
        self.source_btn.set_selection(sel)
        cfg.set(cfg.captureSelection, sel.to_dict())
        self.selectionChanged.emit(sel)

    # -- run state ------------------------------------------------------------
    def _on_start_clicked(self) -> None:
        if self._running:
            self.stopRequested.emit()
        else:
            self.startRequested.emit()

    def set_running(self, running: bool, paused: bool = False) -> None:
        self._running = running
        if running:
            self.start_btn.setText(self.tr("Stop"))
            self.start_btn.setIcon(FluentIcon.PAUSE_BOLD)
            self.state_label.setText(self.tr("Switching model…") if paused else self.tr("Listening"))
            self.view.set_placeholder(self.tr("Listening… captions will appear here."))
        else:
            self.start_btn.setText(self.tr("Start"))
            self.start_btn.setIcon(FluentIcon.PLAY_SOLID)
            self.state_label.setText(self.tr("Stopped"))
            self.latency_label.setText("")
            self.level_bar.setValue(0)
            self.view.set_placeholder(self.tr("Press Start to caption the selected audio."))

    def set_latency(self, seconds: float) -> None:
        self.latency_label.setText(self.tr("Latency {s:.1f} s").format(s=seconds))

    def set_levels(self, levels: list) -> None:
        db = max((lv[2] for lv in levels), default=-120.0)
        self.level_bar.setValue(int(max(0.0, min(1.0, (db + 60) / 60)) * 100))
        if self._running and not levels and self.selection.mode == "apps":
            self.state_label.setText(self.tr("Waiting for the selected apps to start"))
        elif self._running:
            self.state_label.setText(self.tr("Listening"))

    def set_overlay_checked(self, on: bool) -> None:
        self.overlay_btn.blockSignals(True)
        self.overlay_btn.setChecked(on)
        self.overlay_btn.blockSignals(False)
