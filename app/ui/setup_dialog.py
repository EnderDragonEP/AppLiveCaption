"""First-time setup: GPU support, a model to download, and the model loaded at start-up.

Shown once at the first start (Settings → First-time setup opens it again). It replaces the start-up
notifications that used to suggest GPU support and point at Settings → Default model.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QButtonGroup, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    ComboBox,
    MessageBoxBase,
    PushButton,
    RadioButton,
    SubtitleLabel,
)

from ..asr import cuda_libs
from ..asr.hardware import GpuInfo, suggest_model, wants_legacy_engine
from ..config import NO_MODEL, PREVIOUS_MODEL
from ..models.catalog import load_catalog
from ..models.storage import is_downloaded
from .download import format_bytes
from .models_page import languages_text


@dataclass
class SetupChoices:
    gpu_support: bool  # download GPU support
    model_id: str | None  # the model to download (or load, if it's downloaded); None: not now
    default_model: str  # Settings → Default model: NO_MODEL, PREVIOUS_MODEL or a model id


class FirstRunDialog(MessageBoxBase):
    """A few steps with Back / Next / Skip. ``choices()`` is what to do once ``exec()`` returned True."""

    def __init__(self, gpu_offer: str | None, gpu_usable: bool, gpu: GpuInfo | None, default_model: str,
                 parent=None):
        """``gpu_offer``: the name of an NVIDIA GPU that GPU support would make usable (shows that step).
        ``gpu_usable``: models already run on the GPU. ``default_model``: the current Default model."""
        super().__init__(parent)
        self._gpu_usable = gpu_usable
        self._gpu = gpu
        self._model_touched = False  # the user picked a model themselves: keep it when the GPU choice changes
        self.widget.setMinimumWidth(560)

        self.step_label = CaptionLabel("", self)
        self.step_label.setTextColor("#707070", "#a0a0a0")
        self.viewLayout.addWidget(self.step_label)
        self.pages = QStackedWidget(self)
        self.viewLayout.addWidget(self.pages)

        # GPU support
        legacy = gpu_offer is not None and wants_legacy_engine(gpu)
        self.gpu_box = CheckBox(self.tr("Download GPU support ({size})").format(
            size=format_bytes(cuda_libs.download_size(legacy) or cuda_libs.DOWNLOAD_SIZE)), self)
        self.gpu_box.setChecked(True)
        self.gpu_box.toggled.connect(self._suggest)
        self.gpu_page = self._page(
            self.tr("Use your NVIDIA GPU"),
            self.tr("Your {gpu} can turn speech into text many times faster than the CPU. It needs NVIDIA's CUDA "
                    "libraries, a one-time download.").format(gpu=gpu_offer or "NVIDIA GPU"),
            [self.gpu_box],
            self.tr("Without it, models run on the CPU: that works too, with a smaller model. You can download it "
                    "later on the Models page."))

        # Model
        self.model_combo = ComboBox(self)
        self.model_combo.setMinimumWidth(360)
        self._model_ids: list[str | None] = []
        for m in load_catalog():
            self._model_ids.append(m.id)
            self.model_combo.addItem(m.name)
        self._model_ids.append(None)
        self.model_combo.addItem(self.tr("Not now, I'll pick one later"))
        self.model_detail = CaptionLabel("", self)
        self.model_detail.setWordWrap(True)
        self.model_combo.currentIndexChanged.connect(self._on_model)
        self.model_combo.currentIndexChanged.connect(lambda _i: setattr(self, "_model_touched", True))
        self.model_page = self._page(
            self.tr("Pick a speech model"),
            self.tr("The model turns speech into text. Bigger models are more accurate but need a faster PC. "
                    "You can add, remove or switch models any time on the Models page."),
            [self.model_combo, self.model_detail])

        # Model at start-up
        self.startup_group = QButtonGroup(self)
        self.startup_none = RadioButton(self.tr("None: I'll pick one in the Model dropdown when I need it"), self)
        self.startup_previous = RadioButton(self.tr("The model I used last"), self)
        self.startup_picked = RadioButton("", self)
        for button in (self.startup_none, self.startup_previous, self.startup_picked):
            self.startup_group.addButton(button)
        # The current setting (when setup runs again); a model id is matched once a model is picked.
        self._default_model = default_model
        (self.startup_previous if default_model == PREVIOUS_MODEL else self.startup_none).setChecked(True)
        self.startup_page = self._page(
            self.tr("Model at start-up"),
            self.tr("Which model should be ready when the app starts? A loaded model takes memory and a few "
                    "seconds to load."),
            [self.startup_none, self.startup_previous, self.startup_picked],
            self.tr("You can change this later in Settings → Default model."))

        self._steps = ([self.gpu_page] if gpu_offer else []) + [self.model_page, self.startup_page]
        for page in (self.gpu_page, self.model_page, self.startup_page):
            if page in self._steps:
                self.pages.addWidget(page)
            else:
                # Not a step here (the GPU works already). As a child of the stack that isn't one of its
                # pages, it would otherwise be drawn on top of the steps.
                page.hide()

        # Buttons: Back | Next (Finish) | Skip
        self.back_button = PushButton(self.tr("Back"), self.buttonGroup)
        self.back_button.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))
        self.buttonLayout.insertWidget(0, self.back_button, 1, Qt.AlignmentFlag.AlignVCenter)
        self.cancelButton.setText(self.tr("Skip"))

        self._suggest()
        self._model_touched = False
        self._go(0)

    def _page(self, title: str, text: str, widgets: list[QWidget], hint: str = "") -> QWidget:
        page = QWidget(self.pages)
        lay = QVBoxLayout(page)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(SubtitleLabel(title, page))
        body = BodyLabel(text, page)
        body.setWordWrap(True)
        lay.addWidget(body)
        lay.addSpacing(4)
        for w in widgets:
            if isinstance(w, ComboBox):
                lay.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)  # (not stretched across the dialog)
            else:
                lay.addWidget(w)
        if hint:
            label = CaptionLabel(hint, page)
            label.setWordWrap(True)
            label.setTextColor("#707070", "#a0a0a0")
            lay.addSpacing(4)
            lay.addWidget(label)
        lay.addStretch(1)
        return page

    # -- steps ----------------------------------------------------------------
    def _go(self, index: int) -> None:
        index = max(0, min(index, len(self._steps) - 1))
        self.pages.setCurrentIndex(index)
        if self._steps[index] is self.startup_page:
            self._update_startup()
        last = index == len(self._steps) - 1
        self.step_label.setText(self.tr("First-time setup · Step {n} of {total}").format(n=index + 1,
                                                                                       total=len(self._steps)))
        self.back_button.setEnabled(index > 0)
        self.yesButton.setText(self.tr("Finish") if last else self.tr("Next"))

    def validate(self) -> bool:
        """Next goes to the following step; Finish (on the last one) closes the dialog."""
        if self.pages.currentIndex() < len(self._steps) - 1:
            self._go(self.pages.currentIndex() + 1)
            return False
        return True

    # -- model ----------------------------------------------------------------
    def _will_use_gpu(self) -> bool:
        return self._gpu_usable or (self.gpu_page in self._steps and self.gpu_box.isChecked())

    def _suggest(self) -> None:
        """Pick the suggested model for the device the models will run on, unless the user chose one."""
        if self._model_touched:
            return
        device = "cuda" if self._will_use_gpu() else "cpu"
        models = load_catalog()
        suggested = suggest_model([(m.id, m.tier) for m in models], device, self._gpu)
        self._suggested = suggested
        index =self._model_ids.index(suggested) if suggested in self._model_ids else len(self._model_ids) - 1
        self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._on_model(index)
        self._model_touched = False

    def _on_model(self, index: int) -> None:
        model_id = self._model_ids[index] if 0 <= index < len(self._model_ids) else None
        info = next((m for m in load_catalog() if m.id == model_id), None)
        if info is None:
            self.model_detail.setText(self.tr("Nothing is downloaded now. The Models page lists every model."))
            return
        parts = [info.description]
        meta = [self.tr("Already downloaded") if is_downloaded(info)
                else self.tr("Download {size}").format(size=format_bytes(info.size_mb * 2**20))]
        meta.append(self.tr("Languages: {langs}").format(langs=languages_text(info)))
        if info.id == getattr(self, "_suggested", None):
            meta.append(self.tr("Recommended for this PC"))
        parts.append(" · ".join(meta))
        self.model_detail.setText("\n".join(p for p in parts if p))

    def _picked(self):
        index = self.model_combo.currentIndex()
        model_id = self._model_ids[index] if 0 <= index < len(self._model_ids) else None
        return next((m for m in load_catalog() if m.id == model_id), None)

    def _update_startup(self) -> None:
        info = self._picked()
        self.startup_picked.setVisible(info is not None)
        if info is not None:
            self.startup_picked.setText(self.tr("Always {name}").format(name=info.name))
            if info.id == self._default_model:
                self.startup_picked.setChecked(True)
        elif self.startup_picked.isChecked():
            self.startup_none.setChecked(True)

    # -- result ---------------------------------------------------------------
    def choices(self) -> SetupChoices:
        info = self._picked()
        if self.startup_picked.isChecked() and info is not None:
            default = info.id
        elif self.startup_previous.isChecked():
            default = PREVIOUS_MODEL
        else:
            default = NO_MODEL
        return SetupChoices(gpu_support=self.gpu_page in self._steps and self.gpu_box.isChecked(),
                            model_id=info.id if info else None, default_model=default)
