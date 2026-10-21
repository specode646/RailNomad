"""Separate city/station selection with searchable, type-specific choices."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QComboBox, QLabel, QCompleter
from .names import NameCatalog
from .widgets import HelpButton


class PlacePicker(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._cities = []
        self._stations = []
        self._initialized = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.kind = QComboBox()
        self.kind.addItem('城市', 'city')
        self.kind.addItem('车站', 'station')
        self.kind.setFixedWidth(90)
        self.place = QComboBox()
        self.place.setEditable(True)
        self.place.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.place.setMinimumContentsLength(16)
        self.place.setMaxVisibleItems(12)
        row.addWidget(self.kind)
        row.addWidget(self.place, 1)
        self.help=HelpButton('城市会包含全部已明确归属的车站；车站只搜索该站。输入中文名称或车站代码，可从建议中选择。','地点选择说明')
        row.addWidget(self.help)
        layout.addLayout(row)
        self.hint = QLabel(self)
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet('color:#526d65;font-size:12px;')
        self.hint.hide()
        self.kind.currentIndexChanged.connect(self.populate)
        self.place.currentIndexChanged.connect(self.update_hint)

    def set_places(self, cities, stations):
        selected = self.selected_place()
        self._cities = list(cities)
        self._stations = list(stations)
        if not self._initialized:
            self.kind.blockSignals(True)
            self.kind.setCurrentIndex(0 if cities else 1)
            self.kind.blockSignals(False)
            self._initialized = bool(cities or stations)
        self.populate()
        if selected and selected[0] == self.kind.currentData():
            self.select_place(*selected)

    def populate(self, *_):
        kind = self.kind.currentData()
        self.place.clear()
        self.place.addItem('请选择城市' if kind == 'city' else '请选择车站', None)
        if kind == 'city':
            for city, count in self._cities:
                self.place.addItem(f'{city}（{count} 个车站）', ('city', city))
            placeholder = '输入城市名称查找'
        else:
            for identity, name, city in self._stations:
                label = f'{name} · {city} [{identity}]' if city else f'{name} [{identity}]'
                self.place.addItem(label, ('station', identity))
            placeholder = '输入站名或车站代码查找'
        self.place.lineEdit().setPlaceholderText(placeholder)
        completer = self.place.completer()
        completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        completer.setFilterMode(Qt.MatchFlag.MatchContains)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.update_hint()

    def update_hint(self, *_):
        if self.kind.currentData() == 'city':
            self.hint.setText('选择城市后，检索其全部已明确归属的车站。' if self._cities else
                              '当前数据尚无明确城市归属，请切换“车站”；城市映射可在数据中心导入。')
        else:
            self.hint.setText('仅检索所选车站，不自动扩展到同城其他站。')
        self.kind.setToolTip(self.hint.text())

    def selected_place(self):
        if self.place.currentText() != self.place.itemText(self.place.currentIndex()):
            catalog=NameCatalog(self._stations)
            try:return self.kind.currentData(),catalog.resolve(self.place.currentText(),self.kind.currentData())
            except ValueError:return None
        value = self.place.currentData()
        return tuple(value) if value else None

    def select_place(self, kind, identity):
        for index in range(1, self.place.count()):
            if tuple(self.place.itemData(index)) == (kind, identity):
                self.place.setCurrentIndex(index)
                return True
        return False

    def currentText(self):
        return self.place.currentText()
