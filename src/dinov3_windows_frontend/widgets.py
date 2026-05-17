from __future__ import annotations

from PyQt5 import QtCore, QtWidgets


class ClosingComboBox(QtWidgets.QComboBox):
    def __init__(self) -> None:
        super().__init__()
        self.activated.connect(lambda _index: self.hidePopup())


class LabeledLineEdit(QtWidgets.QWidget):
    def __init__(self, label: str, text: str = "") -> None:
        super().__init__()
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(6)
        self.label = QtWidgets.QLabel(label)
        self.edit = QtWidgets.QLineEdit(text)
        layout.addWidget(self.label, 0, 0)
        layout.addWidget(self.edit, 0, 1)
        layout.setColumnStretch(1, 1)

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)

