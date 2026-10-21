"""Searchable checkbox selection; filtering never discards hidden selections."""
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,
    QListWidget,QListWidgetItem,QPushButton,QDialogButtonBox)


class SelectionDialog(QDialog):
    def __init__(self,catalog,kind,selected=(),search='',help_text='',parent=None):
        super().__init__(parent);self.resize(560,580)
        self.setWindowTitle('多选'+{'station':'车站','train':'车次','city':'城市'}[kind])
        self.selected=list(dict.fromkeys(selected));self.labels={}
        layout=QVBoxLayout(self)
        if help_text:
            label=QLabel(help_text);label.setWordWrap(True);layout.addWidget(label)
        self.search=QLineEdit();self.search.setPlaceholderText('输入中文名称或代码筛选');layout.addWidget(self.search)
        self.list=QListWidget();self.list.setUniformItemSizes(True);layout.addWidget(self.list,1)
        self.list.blockSignals(True)
        choices=[(catalog.resolve(label,kind),label) for label in catalog.choices(kind)]
        present={value for value,label in choices}
        choices.extend((value,value) for value in self.selected if value not in present)
        for value,label in choices:
            if value in self.labels:continue
            self.labels[value]=label
            item=QListWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,value)
            item.setFlags(item.flags()|Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if value in self.selected else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.list.blockSignals(False);self.list.itemChanged.connect(self.toggle)
        self.visible_count=self.list.count()
        row=QHBoxLayout()
        for title,action in [('全选当前显示',self.select_visible),('清空已选',self.clear_selected)]:
            button=QPushButton(title);button.clicked.connect(action);row.addWidget(button)
        self.summary=QLabel();row.addWidget(self.summary,1);layout.addLayout(row)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText('确定')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        self.delay=QTimer(self);self.delay.setSingleShot(True);self.delay.setInterval(100)
        self.delay.timeout.connect(self.filter);self.search.textChanged.connect(lambda _:self.delay.start())
        self.search.setText(search);self.filter();self.search.setFocus()

    def toggle(self,item):
        value=item.data(Qt.ItemDataRole.UserRole)
        if item.checkState()==Qt.CheckState.Checked:
            if value not in self.selected:self.selected.append(value)
        elif value in self.selected:self.selected.remove(value)
        self.update_summary()

    def filter(self):
        term=self.search.text().strip().casefold()
        self.visible_count=0
        for index in range(self.list.count()):
            item=self.list.item(index);visible=term in item.text().casefold();item.setHidden(not visible)
            self.visible_count+=visible
        self.update_summary()

    def update_summary(self):
        self.summary.setText(f'已选 {len(self.selected)} 项 · 显示 {self.visible_count} 项')

    def select_visible(self):
        self.delay.stop();self.filter()
        selected=set(self.selected);self.list.blockSignals(True)
        for index in range(self.list.count()):
            item=self.list.item(index)
            if not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked);value=item.data(Qt.ItemDataRole.UserRole)
                if value not in selected:self.selected.append(value);selected.add(value)
        self.list.blockSignals(False);self.update_summary()

    def clear_selected(self):
        self.list.blockSignals(True)
        for index in range(self.list.count()):self.list.item(index).setCheckState(Qt.CheckState.Unchecked)
        self.list.blockSignals(False);self.selected.clear();self.update_summary()

    def selected_text(self):return '，'.join(self.labels[value] for value in self.selected)
