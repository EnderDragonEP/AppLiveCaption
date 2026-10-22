"""Dialogs for the model list: adding a model, and a confirmation with an extra option."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CheckBox,
    FluentIcon,
    IndeterminateProgressRing,
    LineEdit,
    MessageBoxBase,
    PushButton,
    SegmentedWidget,
    SubtitleLabel,
)

from ..models.custom import CustomModelError, inspect_folder, inspect_repo
from .async_utils import run_async

HF_KEY, FOLDER_KEY = "huggingface", "folder"


class ConfirmDialog(MessageBoxBase):
    """A yes/no question with an optional checkbox (``option``), e.g. "Also delete the files"."""

    def __init__(self, title: str, content: str, yes_text: str, option: str = "", option_checked: bool = True,
                 parent=None):
        super().__init__(parent)
        self.viewLayout.addWidget(SubtitleLabel(title, self))
        label = BodyLabel(content, self)
        label.setWordWrap(True)
        self.viewLayout.addWidget(label)
        self.has_option = bool(option)
        self.option = CheckBox(option, self)
        self.option.setChecked(option_checked)
        self.option.setVisible(self.has_option)
        self.viewLayout.addWidget(self.option)
        self.yesButton.setText(yes_text)
        self.cancelButton.setText(self.tr("Cancel"))
        self.widget.setMinimumWidth(460)

    @property
    def option_checked(self) -> bool:
        return self.has_option and self.option.isChecked()  # (isVisible() is False once the dialog has closed)


class AddModelDialog(MessageBoxBase):
    """Asks for a Hugging Face repository or a local folder and checks it before closing.

    After ``exec()`` returned True, ``entry`` is the catalog entry to add.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.entry: dict | None = None
        self.viewLayout.addWidget(SubtitleLabel(self.tr("Add a model"), self))
        intro = BodyLabel(self.tr("Only faster-whisper (CTranslate2) models work: the files model.bin, "
                                  "config.json and tokenizer.json must be there."), self)
        intro.setWordWrap(True)
        self.viewLayout.addWidget(intro)

        self.pivot = SegmentedWidget(self)
        self.pivot.addItem(HF_KEY, self.tr("Hugging Face"))
        self.pivot.addItem(FOLDER_KEY, self.tr("Folder on this PC"))
        self.pivot.setCurrentItem(HF_KEY)
        self.pivot.currentItemChanged.connect(self._on_source)
        self.viewLayout.addWidget(self.pivot, 0, Qt.AlignmentFlag.AlignLeft)

        self.pages = QStackedWidget(self)
        hf = QWidget(self.pages)
        hf_lay = QVBoxLayout(hf)
        hf_lay.setContentsMargins(0, 0, 0, 0)
        hf_lay.setSpacing(6)
        self.repo_edit = LineEdit(hf)
        self.repo_edit.setPlaceholderText("Systran/faster-whisper-large-v3")
        self.repo_edit.setClearButtonEnabled(True)
        hf_lay.addWidget(self.repo_edit)
        hf_lay.addWidget(self._hint(self.tr("The repository's ID or its huggingface.co link. It's downloaded "
                                            "from the Models page afterwards."), hf))
        self.pages.addWidget(hf)

        folder = QWidget(self.pages)
        folder_lay = QVBoxLayout(folder)
        folder_lay.setContentsMargins(0, 0, 0, 0)
        folder_lay.setSpacing(6)
        row = QHBoxLayout()
        self.folder_edit = LineEdit(folder)
        self.folder_edit.setReadOnly(True)
        self.folder_edit.setPlaceholderText(self.tr("Choose the folder that holds the model"))
        self.browse_btn = PushButton(FluentIcon.FOLDER, self.tr("Browse…"), folder)
        self.browse_btn.clicked.connect(self._browse)
        row.addWidget(self.folder_edit, 1)
        row.addWidget(self.browse_btn)
        folder_lay.addLayout(row)
        folder_lay.addWidget(self._hint(self.tr("The files are copied into the app's models folder, so the "
                                                "original can be moved or deleted afterwards."), folder))
        self.pages.addWidget(folder)
        self.viewLayout.addWidget(self.pages)

        self.name_edit = LineEdit(self)
        self.name_edit.setPlaceholderText(self.tr("Name in the list (optional)"))
        self.name_edit.setClearButtonEnabled(True)
        name_lay = QVBoxLayout()
        name_lay.setSpacing(6)
        name_lay.addWidget(self.name_edit)
        name_lay.addWidget(self._hint(self.tr("Shown on the Models page and in the model dropdowns. Leave it empty "
                                              "to use the repository's or folder's name."), self))
        self.viewLayout.addLayout(name_lay)

        self.status_row = QWidget(self)  # hidden while there's nothing to say, so it takes no space
        status_lay = QHBoxLayout(self.status_row)
        status_lay.setContentsMargins(0, 0, 0, 0)
        self.ring = IndeterminateProgressRing(self.status_row)
        self.ring.setFixedSize(20, 20)
        self.ring.setStrokeWidth(3)
        self.ring.hide()
        self.status = CaptionLabel("", self.status_row)
        self.status.setWordWrap(True)
        status_lay.addWidget(self.ring)
        status_lay.addWidget(self.status, 1)
        self.status_row.hide()
        self.viewLayout.addWidget(self.status_row)

        self.yesButton.setText(self.tr("Add"))
        self.cancelButton.setText(self.tr("Cancel"))
        self.widget.setMinimumWidth(520)
        self.repo_edit.setFocus()
        self._checking = False

    @staticmethod
    def _hint(text: str, parent) -> CaptionLabel:
        label = CaptionLabel(text, parent)
        label.setWordWrap(True)
        label.setTextColor("#707070", "#a0a0a0")
        return label

    # -- inputs -------------------------------------------------------------
    @property
    def source(self) -> str:
        return self.pivot.currentRouteKey() or HF_KEY

    def _on_source(self, key: str) -> None:
        self.pages.setCurrentIndex(0 if key == HF_KEY else 1)
        self._set_status("")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self.window(), self.tr("Choose the model folder"),
                                                  self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)
            self._set_status("")

    def _set_status(self, text: str, error: bool = False, busy: bool = False) -> None:
        self.status.setText(text)
        self.status.setTextColor("#c42b1c" if error else "#707070", "#ff99a4" if error else "#a0a0a0")
        self.ring.setVisible(busy)
        self.status_row.setVisible(bool(text))

    # -- checking -------------------------------------------------------------
    def validate(self) -> bool:
        """Check the model in the background; the dialog closes itself when it's fine."""
        if self._checking:
            return False
        name = self.name_edit.text()
        if self.source == HF_KEY:
            text = self.repo_edit.text()
            if not text.strip():
                self._set_status(self.tr("Enter a Hugging Face repository first."), error=True)
                return False
            self._set_status(self.tr("Checking Hugging Face…"), busy=True)

            def check():
                return inspect_repo(text, name)
        else:
            path = self.folder_edit.text()
            if not path:
                self._set_status(self.tr("Choose a folder first."), error=True)
                return False
            self._set_status(self.tr("Checking the folder…"), busy=True)

            def check():
                return inspect_folder(path, name)

        self._set_busy(True)

        def run():
            try:
                return "ok", check()
            except CustomModelError as exc:
                return "error", str(exc)

        run_async(run, self._on_checked, self, on_error=lambda msg: self._on_checked(("error", msg)))
        return False

    def _set_busy(self, busy: bool) -> None:
        self._checking = busy
        for w in (self.yesButton, self.repo_edit, self.folder_edit, self.browse_btn, self.name_edit, self.pivot):
            w.setEnabled(not busy)

    def _on_checked(self, result) -> None:
        self._set_busy(False)
        kind, value = result
        if kind == "ok":
            self.entry = value
            self.accept()
        else:
            self._set_status(value, error=True)
