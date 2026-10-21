"""Plain-language duration choices for the planning form."""
from PySide6.QtCore import QTime,Signal
from PySide6.QtWidgets import QWidget,QHBoxLayout,QComboBox,QTimeEdit,QLabel,QAbstractSpinBox


class DaytimeLimit(QWidget):
    changed=Signal()

    def __init__(self,reference=None):
        super().__init__()
        self.reference=reference
        layout=QHBoxLayout(self);layout.setContentsMargins(0,0,0,0)
        self.choice=QComboBox();self.choice.addItem('不限',None)
        if reference is not None:self.choice.addItem('和平时一样','same')
        self.choice.addItem('最多','custom')
        self.duration=QTimeEdit(QTime(3,0));self.duration.setDisplayFormat("H' 小时 'mm' 分钟'")
        self.duration.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.inherited=QLabel()
        layout.addWidget(self.choice);layout.addWidget(self.duration);layout.addWidget(self.inherited);layout.addStretch()
        self.choice.currentIndexChanged.connect(self.update_choice)
        self.duration.timeChanged.connect(self.update_choice)
        if reference is not None:reference.changed.connect(self.update_choice)
        self.update_choice()

    def update_choice(self,*_):
        mode=self.choice.currentData()
        self.duration.setVisible(mode=='custom')
        self.inherited.setVisible(mode=='same')
        if self.reference is not None:
            value=self.reference.value()
            self.inherited.setText('不限' if value is None else f'{value//60} 小时 {value%60} 分钟')
        self.changed.emit()

    def value(self):
        mode=self.choice.currentData()
        if mode is None:return None
        if mode=='same':return self.reference.value()
        value=self.duration.time()
        return value.hour()*60+value.minute()

    def setValue(self,value):
        if value is None:
            self.choice.setCurrentIndex(0)
        else:
            self.duration.setTime(QTime(value//60,value%60))
            self.choice.setCurrentIndex(self.choice.findData('custom'))
