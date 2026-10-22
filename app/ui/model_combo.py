"""Model dropdown that shows models which aren't downloaded grayed out."""

from __future__ import annotations

from qfluentwidgets import ComboBox, IndicatorMenuItemDelegate

DIMMED_OPACITY = 0.4


class _DimmedItemDelegate(IndicatorMenuItemDelegate):
    """Paints some rows faded, like disabled items, while they stay clickable."""

    def __init__(self, dimmed: set[int], parent=None):
        super().__init__(parent)
        self.dimmed = dimmed

    def paint(self, painter, option, index):
        if index.row() not in self.dimmed:
            return super().paint(painter, option, index)
        painter.save()
        painter.setOpacity(DIMMED_OPACITY)
        super().paint(painter, option, index)
        painter.restore()


class ModelComboBox(ComboBox):
    """Dropdown whose dimmed items are grayed out in the menu but still selectable.

    Disabling the items would gray them out too, but disabled items can't be
    picked, and picking a model that isn't downloaded opens its card on the
    Models page, where it can be downloaded.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dimmed: set[int] = set()

    def set_item_dimmed(self, index: int, dimmed: bool) -> None:
        if dimmed:
            self._dimmed.add(index)
        else:
            self._dimmed.discard(index)

    def is_item_dimmed(self, index: int) -> bool:
        return index in self._dimmed

    def clear(self) -> None:
        super().clear()
        self._dimmed.clear()

    def setPlaceholderText(self, text: str) -> None:  # noqa: N802 - Qt name
        # qfluentwidgets also shows the placeholder over item 0 when it's selected; only show
        # it when nothing is (setCurrentIndex(-1)).
        self._placeholderText = text
        if self.currentIndex() < 0:
            self._updateTextState(True)
            self.setText(text)

    def _createComboMenu(self):  # noqa: N802 - qfluentwidgets hook
        menu = super()._createComboMenu()
        # Menu rows are created in item order, so row numbers are item indexes.
        menu.view.setItemDelegate(_DimmedItemDelegate(set(self._dimmed), menu.view))
        return menu
