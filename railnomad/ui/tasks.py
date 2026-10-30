import json
import sqlite3
from pathlib import Path
from ..domain.model import outdated_search_rules
from .names import NameCatalog
from .widgets import Disclosure
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QTableWidget,QTableWidgetItem,QPushButton,
                              QDialog,QDialogButtonBox,QScrollArea,QGroupBox,QFormLayout,QGridLayout,QPlainTextEdit,QApplication,QSizePolicy)


MISSING = '未记录'


def setting_sections(request, catalog):
    def value(key, formatter=str):
        return formatter(request[key]) if key in request else MISSING

    def duration(minutes):
        if minutes is None:return '不限'
        if type(minutes) is not int:return str(minutes)
        hours, remaining = divmod(minutes, 60)
        return ' '.join(part for part in (f'{hours} 小时' if hours else '', f'{remaining} 分钟' if remaining or not hours else '') if part)

    def clock(minutes):
        return f'{minutes//60:02d}:{minutes%60:02d}' if type(minutes) is int else str(minutes)

    def window(start, end):
        if start not in request or end not in request:return MISSING
        first, last = request[start], request[end]
        overnight = type(first) is int and type(last) is int and last < first
        return f'{clock(first)} — {"次日 " if overnight else ""}{clock(last)}'

    def place(key):
        if key not in request:return MISSING
        identity = request[key]
        if request.get(key+'_kind') == 'station':return f'{catalog.station_name(identity)} [{identity}] · 车站'
        return str(identity) + (' · 城市' if request.get(key+'_kind') == 'city' else '')

    def choices(key, stations=False):
        return value(key, lambda items: '、'.join(f'{catalog.station_name(item)} [{item}]' if stations else str(item) for item in items) or '未指定')

    def day_limit(key):
        if request.get('limit_daylight') is False:return '不限（白天乘车限制已关闭）'
        return value(key, duration)

    middle = day_limit('max_daylight_on_train')
    if request.get('limit_daylight') is not False and request.get('max_daylight_on_train') is None:
        if all(key in request for key in ('daylight_start','daylight_end','min_off_train')):
            middle = duration(request['daylight_end']-request['daylight_start']-request['min_off_train'])+'（按历史离车时长换算）'
    short_enabled = request.get('allow_short_hsr')
    sections = [
        ('行程', [('出发地',place('origin')),('目的地',place('destination')),
                  ('出发日期',value('departure_date',lambda day:day or '全部适用日期')),
                  ('卧铺夜数',f"{request['min_nights']} — {request['max_nights']} 晚" if all(k in request for k in ('min_nights','max_nights')) else MISSING),
                  ('连续卧铺夜',value('consecutive',lambda enabled:'要求连续' if enabled else '允许空夜')),
                  ('最短卧铺乘车',value('min_sleeper_minutes',duration)),
                  ('学生参考预算',value('budget',lambda amount:f'¥{amount:,.2f}'))]),
        ('城市与车站', [('必须停留城市',choices('required_cities')),
                     ('必停城市关系',value('required_city_mode',lambda mode:{'AND':'全部满足（AND）','OR':'至少一个（OR）'}.get(mode,str(mode)))),
                     ('禁止经过城市',choices('forbidden_cities')),('优先停留城市',choices('preferred_cities')),
                     ('优先车站',choices('preferred_stations',True)),('允许的车站',choices('allowed_stations',True)),
                     ('其他偏好',value('other_preferences',lambda text:text or '未指定'))]),
        ('到发时间', [('舒适到达',window('target_arrive_start','target_arrive_end')),
                    ('舒适出发',window('target_depart_start','target_depart_end')),
                    ('极限到达',window('arrive_start','arrive_end')),('极限出发',window('depart_start','depart_end'))]),
        ('白天乘车', [('白天统计时段',window('daylight_start','daylight_end')),
                    ('限制开关',value('limit_daylight',lambda enabled:'已开启' if enabled else '已关闭')),
                    ('旅途中每天最多',middle),('出发当天最多',day_limit('first_daylight_limit')),
                    ('抵达当天最多',day_limit('last_daylight_limit'))]),
        ('夜车换乘', [('同站最短停留',value('same_station_gap',duration)),
                    ('同城换站最短停留',value('same_city_gap',duration))]),
        ('短高铁', [('允许短高铁',value('allow_short_hsr',lambda enabled:'允许' if enabled else '不允许'))])]
    if short_enabled:
        sections[-1][1].extend([('单段最长乘车',value('short_hsr_max',duration)),
                               ('整段允许时段',window('target_arrive_start','target_depart_end')),
                               ('同站最短衔接',value('short_same_station_gap',duration)),
                               ('同城换站最短衔接',value('short_same_city_gap',duration))])
    return sections


class TaskSettingsDialog(QDialog):
    def __init__(self, main, run):
        super().__init__(main)
        self.setObjectName('taskSettingsDialog');self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('检索设置');self.resize(900,760)
        request=json.loads(run['request'])
        snapshots=main.store.rows('SELECT id,tag,path FROM snapshots WHERE id=?',(run['snapshot'],))
        snapshot=snapshots[0] if snapshots else None
        catalog=NameCatalog()
        if snapshot and Path(snapshot['path']).is_file():
            try:catalog=NameCatalog.load(main.store,run['snapshot'])
            except (OSError,sqlite3.Error,ValueError):pass
        sections=setting_sections(request,catalog)
        sections.append(('任务记录',[('建立时间',run['created']),('任务编号',run['id']),
                                     ('运行图版本',snapshot['tag'] if snapshot else '快照记录不存在'),
                                     ('快照编号',run['snapshot']),('规则版本',request.get('rules_version',MISSING))]))
        self.settings_text='\n\n'.join(title+'\n'+'\n'.join(f'{key}：{text}' for key,text in rows) for title,rows in sections)
        layout=QVBoxLayout(self)
        notice=QLabel('以下为该任务创建时保存的设置。'+(' 历史任务的缺失字段显示为“未记录”。' if outdated_search_rules(request) else ''))
        notice.setWordWrap(True);layout.addWidget(notice)
        scroll=QScrollArea();self.scroll_area=scroll;scroll.setWidgetResizable(True);body=QWidget();body_layout=QVBoxLayout(body)
        groups=QGridLayout();groups.setColumnStretch(0,1);groups.setColumnStretch(1,1)
        for position,(title,rows) in enumerate(sections):
            box=QGroupBox(title);form=QFormLayout(box);form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
            form.setFormAlignment(Qt.AlignmentFlag.AlignTop)
            for key,text in rows:
                label=QLabel(str(text));label.setTextFormat(Qt.TextFormat.PlainText);label.setWordWrap(True)
                label.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
                label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                form.addRow(key+'：',label)
            groups.addWidget(box,position//2,position%2,1,2 if title=='任务记录' else 1)
        body_layout.addLayout(groups)
        self.raw_parameters=QPlainTextEdit(json.dumps(request,ensure_ascii=False,indent=2,sort_keys=True))
        self.raw_parameters.setReadOnly(True);self.raw_parameters.setMaximumHeight(220)
        body_layout.addWidget(Disclosure('原始参数',self.raw_parameters));body_layout.addStretch()
        scroll.setWidget(body);layout.addWidget(scroll)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText('关闭')
        buttons.rejected.connect(self.reject)
        copy=buttons.addButton('复制设置',QDialogButtonBox.ButtonRole.ActionRole)
        copy.clicked.connect(lambda:QApplication.clipboard().setText(self.settings_text+'\n\n原始参数\n'+self.raw_parameters.toPlainText()))
        layout.addWidget(buttons)


class TasksPage(QWidget):
    def __init__(self,main):
        super().__init__();self.main=main
        layout=QVBoxLayout(self)
        title=QLabel('检索任务');title.setObjectName('heading');layout.addWidget(title)
        row=QHBoxLayout()
        for text,fn in [('刷新',self.refresh),('查看设置',self.settings),('打开结果',self.open),('继续 / 重试',self.resume),('删除任务',self.remove)]:
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

    def settings(self):
        try:
            identity=self.selected()['id']
            rows=self.main.store.rows('SELECT id,created,snapshot,request FROM runs WHERE id=?',(identity,))
            if not rows:raise ValueError('该任务已删除，请刷新任务列表')
            dialog=TaskSettingsDialog(self.main,rows[0])
            self.main.watch_dialog(dialog,identity,refresh=lambda:None)
            dialog.show()
            return dialog
        except (ValueError,IndexError,OSError,sqlite3.Error) as exc:self.main.message(str(exc))

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
