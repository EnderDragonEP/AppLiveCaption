"""Keywords page: the keywords to watch for, what happens when one is heard, and the recent matches."""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QListWidgetItem, QVBoxLayout, QWidget
from qfluentwidgets import (
    BodyLabel,
    CaptionLabel,
    CardWidget,
    CheckBox,
    CustomColorSettingCard,
    FlowLayout,
    FluentIcon,
    IconWidget,
    LineEdit,
    ListWidget,
    PrimaryPushButton,
    PushButton,
    ScrollArea,
    SettingCardGroup,
    StrongBodyLabel,
    SubtitleLabel,
    SwitchButton,
    SwitchSettingCard,
    TitleLabel,
    ToolTipFilter,
    TransparentToolButton,
)

from ..config import cfg
from ..core.keywords import FUZZY, describe, latin_could_be_pinyin
from ..i18n import tr
from .keyword_service import KeywordService

# The 模糊音 pairs as Zhuyin, next to their Pinyin.
_ZHUYIN = {"zh/z": "ㄓ/ㄗ", "ch/c": "ㄔ/ㄘ", "sh/s": "ㄕ/ㄙ", "n/l": "ㄋ/ㄌ", "f/h": "ㄈ/ㄏ", "an/ang": "ㄢ/ㄤ",
           "en/eng": "ㄣ/ㄥ", "in/ing": "ㄧㄣ/ㄧㄥ"}


def _how(keyword: str, latin_sound: bool = False) -> str:
    """How a keyword is matched, in words."""
    try:
        sound = describe(keyword, latin_sound)
    except Exception:  # noqa: BLE001 - pypinyin missing
        sound = ""
    return tr("Also matches what sounds like: {sound}").format(sound=sound) if sound else tr("Matches the text, in any case")


def _plain_label(label):
    """Show keywords as typed, never as rich text (a keyword like "<b>x" stays literal)."""
    label.setTextFormat(Qt.TextFormat.PlainText)
    return label


class KeywordRow(CardWidget):
    """One keyword: its text, how it's matched, a switch and a delete button. Plain letters that spell
    Pinyin ("taibei", but also "China") also get a "Match by sound" check box, off by default."""

    def __init__(self, keyword: dict, service: KeywordService, parent=None):
        super().__init__(parent)
        self.text = keyword["text"]
        self.sound_box = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(20, 10, 12, 10)
        lay.setSpacing(12)
        icon = IconWidget(FluentIcon.TAG, self)
        icon.setFixedSize(16, 16)
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(_plain_label(StrongBodyLabel(self.text, self)))
        self.how = _plain_label(CaptionLabel("", self))
        self.how.setTextColor("#707070", "#a0a0a0")
        col.addWidget(self.how)
        lay.addLayout(col, 1)
        if latin_could_be_pinyin(self.text):
            self.sound_box = CheckBox(self.tr("Match by sound"), self)
            self.sound_box.setToolTip(self.tr("Treat it as Pinyin, so Chinese words that sound like it match too"))
            self.sound_box.installEventFilter(ToolTipFilter(self.sound_box))
            self.sound_box.toggled.connect(lambda on: service.set_sound(self.text, on))
            lay.addWidget(self.sound_box)
        self.switch = SwitchButton(self)
        self.switch.setOnText(self.tr("On"))
        self.switch.setOffText(self.tr("Off"))
        self.switch.setChecked(keyword.get("enabled", True))
        self.switch.checkedChanged.connect(lambda on: service.set_enabled(self.text, on))
        lay.addWidget(self.switch)
        delete = TransparentToolButton(FluentIcon.DELETE, self)
        delete.setToolTip(self.tr("Remove"))
        delete.installEventFilter(ToolTipFilter(delete))
        delete.clicked.connect(lambda: service.remove(self.text))
        lay.addWidget(delete)
        self.update_from(keyword)

    def update_from(self, keyword: dict) -> None:
        """Show the keyword's current switches (the list was saved again)."""
        sound = keyword.get("sound", False)
        for widget, on in ((self.switch, keyword.get("enabled", True)), (self.sound_box, sound)):
            if widget is not None:
                widget.blockSignals(True)
                widget.setChecked(on)
                widget.blockSignals(False)
        self.how.setText(_how(self.text, sound))


class FuzzyCard(CardWidget):
    """The 模糊音 pairs, as check boxes."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        head = QHBoxLayout()
        head.setSpacing(16)
        icon = IconWidget(FluentIcon.LANGUAGE, self)
        icon.setFixedSize(16, 16)
        head.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addWidget(BodyLabel(self.tr("Similar sounds (模糊音)"), self))
        hint = CaptionLabel(self.tr("Chinese keywords already match any tone. Tick the sounds that should also count "
                                    "as the same, for speakers or models that mix them up."), self)
        hint.setWordWrap(True)
        hint.setTextColor("#606060", "#d0d0d0")
        col.addWidget(hint)
        head.addLayout(col, 1)
        lay.addLayout(head)
        boxes = QWidget(self)
        flow = FlowLayout(boxes, needAni=False)
        flow.setContentsMargins(32, 0, 0, 0)
        flow.setHorizontalSpacing(20)
        flow.setVerticalSpacing(8)
        on = set(cfg.get(cfg.keywordFuzzy))
        self.boxes = {}
        for pair in FUZZY:
            box = CheckBox(f"{pair.replace('/', ' = ')}  ({_ZHUYIN[pair].replace('/', ' = ')})", boxes)
            box.setChecked(pair in on)
            box.toggled.connect(self._save)
            flow.addWidget(box)
            self.boxes[pair] = box
        lay.addWidget(boxes)

    def _save(self) -> None:
        cfg.set(cfg.keywordFuzzy, [p for p, box in self.boxes.items() if box.isChecked()])


class KeywordsPage(ScrollArea):
    testNotificationRequested = Signal()  # the main window shows it (it owns the tray icon)

    def __init__(self, service: KeywordService, parent=None):
        super().__init__(parent)
        self.service = service
        self.setObjectName("keywordsPage")
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea{background:transparent;border:none}")
        body = QWidget()
        body.setStyleSheet("background:transparent")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(36, 24, 36, 24)
        lay.setSpacing(10)
        lay.addWidget(TitleLabel(self.tr("Keywords"), body))
        intro = CaptionLabel(self.tr("Get an alert when a word or a name is said. Chinese keywords also match words "
                                     "that sound the same, and can be typed as 漢字, Zhuyin or Pinyin."), body)
        intro.setWordWrap(True)
        lay.addWidget(intro)

        # Adding a keyword
        add = CardWidget(body)
        add_lay = QVBoxLayout(add)
        add_lay.setContentsMargins(16, 12, 16, 12)
        add_lay.setSpacing(6)
        row = QHBoxLayout()
        self.edit = LineEdit(add)
        self.edit.setPlaceholderText(self.tr("A word or a name, e.g. Ender, 台北, ㄊㄞˊ ㄅㄟˇ or taibei"))
        self.edit.setClearButtonEnabled(True)
        self.edit.returnPressed.connect(self._add)
        self.edit.textChanged.connect(self._preview)
        row.addWidget(self.edit, 1)
        self.add_btn = PrimaryPushButton(FluentIcon.ADD, self.tr("Add"), add)
        self.add_btn.clicked.connect(self._add)
        row.addWidget(self.add_btn)
        add_lay.addLayout(row)
        self.preview = _plain_label(CaptionLabel("", add))
        self.preview.setTextColor("#707070", "#a0a0a0")
        add_lay.addWidget(self.preview)
        lay.addWidget(add)

        self.rows_layout = QVBoxLayout()
        self.rows_layout.setSpacing(6)
        lay.addLayout(self.rows_layout)
        self.empty = CaptionLabel(self.tr("No keywords yet."), body)
        self.empty.setTextColor("#707070", "#a0a0a0")
        lay.addWidget(self.empty)
        lay.addSpacing(14)

        # What happens on a match
        g = SettingCardGroup(self.tr("When a keyword is heard"), body)
        g.addSettingCard(SwitchSettingCard(FluentIcon.HIGHTLIGHT, self.tr("Highlight it"),
                                           self.tr("In the live captions, the overlay and History"),
                                           cfg.keywordHighlight, g))
        g.addSettingCard(CustomColorSettingCard(cfg.keywordColor, FluentIcon.PALETTE, self.tr("Highlight color"),
                                                self.tr("The color keywords are marked with"), g))
        sound = SwitchSettingCard(FluentIcon.RINGER, self.tr("Play a sound"),
                                  self.tr("Windows' notification sound, once per caption line"), cfg.keywordSound, g)
        test = PushButton(self.tr("Play"), sound)
        test.clicked.connect(lambda: service.play_sound(force=True))
        sound.hBoxLayout.insertWidget(sound.hBoxLayout.indexOf(sound.switchButton), test)
        sound.hBoxLayout.insertSpacing(sound.hBoxLayout.indexOf(sound.switchButton), 16)
        g.addSettingCard(sound)
        notify = SwitchSettingCard(FluentIcon.MESSAGE, self.tr("Show a notification"),
                                   self.tr("A Windows notification while the app isn't the active window"),
                                   cfg.keywordNotify, g)
        test = PushButton(self.tr("Show"), notify)
        test.clicked.connect(self.testNotificationRequested)
        notify.hBoxLayout.insertWidget(notify.hBoxLayout.indexOf(notify.switchButton), test)
        notify.hBoxLayout.insertSpacing(notify.hBoxLayout.indexOf(notify.switchButton), 16)
        g.addSettingCard(notify)
        lay.addWidget(g)
        lay.addSpacing(14)

        lay.addWidget(SubtitleLabel(self.tr("Chinese sound matching"), body))
        lay.addWidget(FuzzyCard(body))
        lay.addSpacing(14)

        # Recent matches
        head = QHBoxLayout()
        head.addWidget(SubtitleLabel(self.tr("Recent matches"), body))
        head.addStretch(1)
        self.clear_btn = PushButton(FluentIcon.BROOM, self.tr("Clear"), body)
        self.clear_btn.clicked.connect(service.clear_recent)
        head.addWidget(self.clear_btn)
        lay.addLayout(head)
        self.recent = ListWidget(body)
        self.recent.setFixedHeight(220)
        self.recent.setWordWrap(True)
        lay.addWidget(self.recent)
        self.no_recent = CaptionLabel(self.tr("Matches show up here while captioning."), body)
        self.no_recent.setTextColor("#707070", "#a0a0a0")
        lay.addWidget(self.no_recent)
        lay.addStretch(1)
        self.setWidget(body)

        self._rows: list[KeywordRow] = []
        service.changed.connect(self.refresh)
        service.recentChanged.connect(self.refresh_recent)
        self.refresh()
        self.refresh_recent()

    # -- the list ----------------------------------------------------------------------------------
    def refresh(self) -> None:
        keywords = self.service.keywords()
        if [r.text for r in self._rows] == [k["text"] for k in keywords]:
            for r, k in zip(self._rows, keywords):  # same list: just the switches
                r.update_from(k)
            return
        for r in self._rows:
            r.hide()
            r.deleteLater()
        self._rows = [KeywordRow(k, self.service, self.widget()) for k in keywords]
        for r in self._rows:
            self.rows_layout.addWidget(r)
        self.empty.setVisible(not keywords)

    def _preview(self, text: str) -> None:
        if not text.strip():
            self.preview.setText("")
        elif latin_could_be_pinyin(text):
            self.preview.setText(self.tr("Matches the text. To match Chinese words that sound like it, tick "
                                         "\"Match by sound\" after adding it."))
        else:
            self.preview.setText(_how(text))

    def _add(self) -> None:
        text = self.edit.text()
        if self.service.add(text):
            self.edit.clear()
        elif text.strip():
            self.preview.setText(self.tr("{keyword} is in the list already.").format(keyword=" ".join(text.split())))

    def refresh_recent(self) -> None:
        self.recent.clear()
        for hit in self.service.recent:
            when = time.strftime("%H:%M:%S", time.localtime(hit.when))
            item = QListWidgetItem(f"{when}  ·  {hit.keyword}  ·  {hit.line}")
            item.setToolTip(hit.line)
            self.recent.addItem(item)
        has = bool(self.service.recent)
        self.recent.setVisible(has)
        self.no_recent.setVisible(not has)
        self.clear_btn.setEnabled(has)
