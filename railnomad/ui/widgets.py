"""Small native disclosures and accessible, on-demand field help."""
from html import escape
from PySide6.QtCore import Qt,QPoint
from PySide6.QtWidgets import QWidget,QHBoxLayout,QVBoxLayout,QLabel,QToolButton,QToolTip


class HelpButton(QToolButton):
    def __init__(self,text,title='说明',parent=None):
        super().__init__(parent);self.setText('?');self.setObjectName('helpButton')
        self.setAccessibleName(title);self.set_help(text)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(lambda:QToolTip.showText(self.mapToGlobal(QPoint(0,self.height())),self.toolTip(),self))

    def set_help(self,text):
        self.setAccessibleDescription(text)
        self.setToolTip('<table width="360"><tr><td>'+escape(text).replace('\n','<br>')+'</td></tr></table>')


def field_label(title,help_text):
    widget=QWidget();row=QHBoxLayout(widget);row.setContentsMargins(0,0,0,0);row.setSpacing(6)
    row.addWidget(QLabel(title));row.addWidget(HelpButton(help_text,title+'说明'));row.addStretch()
    return widget


class Disclosure(QWidget):
    def __init__(self,title,body,parent=None):
        super().__init__(parent);layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(8)
        self.toggle=QToolButton();self.toggle.setObjectName('disclosure');self.toggle.setText(title)
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon);self.toggle.setCheckable(True)
        self.body=body;layout.addWidget(self.toggle);layout.addWidget(body)
        self.toggle.toggled.connect(self.set_expanded);self.set_expanded(False)

    def set_expanded(self,expanded):
        self.toggle.setChecked(expanded);self.body.setVisible(expanded)
        self.toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
