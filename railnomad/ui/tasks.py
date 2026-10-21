import json
from ..domain.model import outdated_search_rules
from .names import NameCatalog
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPushButton)


class TasksPage(QWidget):
    def __init__(self,main):
        super().__init__();self.main=main
        layout=QVBoxLayout(self)
        title=QLabel('检索任务');title.setObjectName('heading');layout.addWidget(title)
        row=QHBoxLayout()
        for text,fn in [('刷新',self.refresh),('打开结果',self.open),('继续 / 重试',self.resume),('删除任务',self.remove)]:
            button=QPushButton(text);button.clicked.connect(fn);row.addWidget(button)
            if text=='删除任务':button.setObjectName('danger')
        layout.addLayout(row)
        self.table=QTableWidget(0,7);self.table.setHorizontalHeaderLabels(['建立时间','起终点','出发日期','夜数','状态','任务 ID','保留方案'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.open)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnHidden(5,True);self.table.verticalHeader().hide()
        layout.addWidget(self.table)
        self.refresh()

    def refresh(self):
        self.runs=self.main.store.rows('SELECT * FROM runs ORDER BY created DESC')
        self.table.setRowCount(len(self.runs))
        labels={'queued':'等待开始','running':'运行中 / 可恢复','completed':'枚举完成','cancelled':'已取消 / 可继续','failed':'失败 / 可重试'}
        labels['limited']='受限停止 · 结果不完整'
        catalogs={}
        counts={r['run_id']:r['n'] for r in self.main.store.rows('SELECT run_id,COUNT(*) n FROM results GROUP BY run_id')}
        for i,run in enumerate(self.runs):
            req=json.loads(run['request'])
            if run['snapshot'] not in catalogs:catalogs[run['snapshot']]=NameCatalog.load(self.main.store,run['snapshot'])
            catalog=catalogs[run['snapshot']]
            origin=catalog.station_name(req['origin']) if req.get('origin_kind')=='station' else req['origin']
            destination=catalog.station_name(req['destination']) if req.get('destination_kind')=='station' else req['destination']
            for col,value in enumerate([run['created'][:19],origin+' → '+destination,req.get('departure_date') or '全部适用日期',f"{req['min_nights']}—{req['max_nights']}",('历史规则' if outdated_search_rules(req) else labels.get(run['status'],run['status'])),run['id'][:12]]):
                self.table.setItem(i,col,QTableWidgetItem(value))
                self.table.item(i,col).setToolTip('任务编号：'+run['id']+'\n建立时间：'+run['created'])
            self.table.setItem(i,6,QTableWidgetItem(f"{counts.get(run['id'],0):,}"))
        self.table.resizeColumnsToContents()

    def selected(self):
        row=self.table.currentRow()
        if row<0:raise ValueError('请选择一个任务')
        return self.runs[row]

    def remove(self):
        rows=sorted({item.row() for item in self.table.selectedItems()})
        if not rows:self.main.message('请选择要删除的检索任务');return
        self.main.delete_catalog('tasks',[self.runs[row]['id'] for row in rows])

    def open(self,*args):
        try:
            self.main.results.load(self.selected()['id']);self.main.pages.setCurrentWidget(self.main.results)
        except Exception as exc:self.main.message(str(exc))

    def resume(self):
        try:
            run=self.selected()
            if outdated_search_rules(json.loads(run['request'])):
                raise ValueError('旧版任务采用不同的检索规则，请使用新建规划重新检索；历史结果仍保留。')
            if run['status']=='completed':raise ValueError('此任务已完成，请打开结果；扩大条件需要新任务。')
            self.main.run_search(run['id'])
        except Exception as exc:self.main.message(str(exc))
