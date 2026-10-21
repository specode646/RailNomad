import json
from PySide6.QtCore import QSize, Qt,QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit, QCheckBox, QPushButton, QTableWidget,
    QTableWidgetItem, QDialog, QTextEdit, QFileDialog, QTabWidget, QListWidget, QListWidgetItem, QGroupBox,QScrollArea,QGridLayout,QToolButton,QMenu)
from ..query import query, validate_filters
from ..storage import encode, now
from ..domain.model import route_empty_nights, outdated_search_rules,daylight_rule_text,comfort_text,time_windows_text
from .itinerary import ItineraryView,RouteCard,money
from ..fares import read_assessment
from .names import NameEdit,NameCatalog
from .widgets import HelpButton,field_label,Disclosure


def clock(seconds):
    day, sec = divmod(seconds,86400)
    return f'第 {day+1} 天 {sec//3600:02d}:{sec//60%60:02d}'


def duration(seconds):
    hours,remainder=divmod(int(seconds),3600)
    minutes,seconds=divmod(remainder,60)
    parts=[]
    if hours:parts.append(f'{hours} 小时')
    if minutes or not parts:parts.append(f'{minutes} 分钟')
    if seconds:parts.append(f'{seconds} 秒')
    return ' '.join(parts)


def sleeper_label(payload,index):
    statuses=[]
    for variant in payload.get('date_evidence_variants',[]):
        legs=variant.get('legs',[])
        evidence=legs[index].get('sleeper_evidence',[]) if index<len(legs) else []
        statuses.append('passed' if evidence and all(e.get('status')=='passed' for e in evidence) else 'pending')
    return '卧铺已核验（见适用日）' if statuses and all(s=='passed' for s in statuses) else '卧铺待核验'


def timeline(payload, request):
    lines = [('【模拟数据】' if payload.get('simulated') else '') + ' → '.join(payload['pattern']),
             f"卧铺 {payload['nights']} 晚 · 参考价 {payload.get('price_range') or '未知'} · {payload['status']} · 不能据此认定可买",
             f"方案 ID：{payload['id']}", '']
    final = payload['legs'][-1]
    earliest = request.get('arrive_start', 360)
    if not final['short'] and final['arr'] % 86400 < earliest * 60:
        lines.insert(0, f"不符合最早到达要求：终点 {clock(final['arr'])}，早于设置的 {earliest//60:02d}:{earliest%60:02d}。此历史结果需要重新检索。")
    empty = route_empty_nights(payload)
    if request.get('consecutive', True) and empty:
        lines.insert(0, '不符合连续卧铺规则：' + '、'.join(f'第 {n+1} 晚' for n in empty) + '没有卧铺覆盖。此历史结果需要重新检索。')
    for i,leg in enumerate(payload['legs']):
        if i:
            previous = payload['legs'][i-1]
            lines.append(f"停留 {duration(leg['dep']-previous['arr'])} · {'同站' if previous['to']==leg['from'] else '同城换站'}（未扣市内接驳）")
        lines.extend([f"{i+1}. {leg['train']} · {'短高铁' if leg['short'] else sleeper_label(payload,i)}",
                      f"   {leg['from_name']} [{leg['from']}]  {clock(leg['dep'])}",
                      f"   → {leg['to_name']} [{leg['to']}]  {clock(leg['arr'])}",
                      f"   历时 {duration(leg['arr']-leg['dep'])}；" + ('车次卧铺夜信息不适用' if leg['short'] else '车次第几晚待核验' if leg.get('train_night') is None else f"车次第 {leg['train_night']} 晚") + '；价格及来源见日期详情', ''])
    daylight_minutes = request['daylight_end']-request['daylight_start']
    lines.append('每日白昼离车（非净游览时长）：')
    for d,seconds in payload['daylight_seconds'].items():
        lines.append(f'第 {int(d)+1} 天：离车 {daylight_minutes-seconds/60:g} 分钟，在车 {seconds/60:g} 分钟')
    lines.append('空夜：' + (', '.join(f'第 {d+1} 晚' for d in empty) or '无'))
    lines.append('共同适用起始日期：' + ', '.join(payload['dates']))
    lines.append('线路证据：所有适用日的物理区段已核验（见逐日来源）' if payload.get('track_status')=='verified_track' else '线路证据：仅站序代理，未完成物理铁路区段核验')
    lines.append('待核验：' + ', '.join(payload['pending']))
    if payload.get('date_evidence_variants'):
        lines.append('日期化价格 / 证据（所有报价仅适用其登车日）：')
        for v in payload['date_evidence_variants']:
            lines.append(f"起始 {v['start_date']} · 学生参考价 {v['price_low']}—{v['price_high']} · 预算 {v['budget_status']} · 核验 {v['status']}")
            if any(l['fares'] for l in v['legs']):
                lines.append(json.dumps(v['legs'],ensure_ascii=False))
    return '\n'.join(lines)


class ResultsPage(QWidget):
    def __init__(self, main, favorites=False):
        super().__init__()
        self.main = main
        self.favorites = favorites
        self.run_id = None
        self.attempted_fare_sync=None
        self.main.worker.finished.connect(lambda:QTimer.singleShot(0,self.sync_cached_fares))
        self.page = 0
        self.rows = []
        layout = QVBoxLayout(self)
        title = QLabel('我的收藏' if favorites else '路线')
        title.setObjectName('heading')
        layout.addWidget(title)
        if favorites:
            self.favorite_tasks=QComboBox()
            self.favorite_tasks.currentIndexChanged.connect(self.choose_favorite_task)
            layout.addWidget(self.favorite_tasks)
        self.info = QLabel('请选择检索任务。')
        self.info.setWordWrap(True)
        info_row=QHBoxLayout();info_row.addWidget(self.info,1)
        self.info_help=HelpButton('选择检索任务后，可查看本次运行图、到发时间及白天在车规则。','检索范围与核验说明')
        info_row.addWidget(self.info_help);layout.addLayout(info_row)
        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self.cities = NameEdit('city')
        self.city_mode = QComboBox()
        self.city_mode.addItems(['AND','OR','顺序'])
        row = QHBoxLayout();row.addWidget(self.cities);row.addWidget(self.city_mode)
        self.city_mode.setFixedWidth(85)
        form.addRow('经过城市', row)
        self.exclude = NameEdit('city');form.addRow('排除城市',self.exclude)
        row = QHBoxLayout()
        self.minimum = QSpinBox();self.minimum.setRange(1,2147483647);self.minimum.setValue(1)
        self.maximum = QSpinBox();self.maximum.setRange(1,2147483647);self.maximum.setValue(365)
        self.budget = QDoubleSpinBox();self.budget.setRange(0,1000000);self.budget.setDecimals(2);self.budget.setValue(1000)
        self.minimum.setFixedWidth(80);self.maximum.setFixedWidth(80)
        row.addWidget(self.minimum);row.addWidget(QLabel('—'));row.addWidget(self.maximum);row.addStretch()
        form.addRow('卧铺晚数',row)
        form.addRow('学生预算',self.budget)
        self.short = QComboBox();self.short.addItems(['短高铁不限','不含短高铁','含短高铁'])
        self.changes = QComboBox();self.changes.addItems(['换站不限','不换站','至多换站一次'])
        self.known = QCheckBox('只看完整参考价')
        self.view = QComboBox();self.view.addItems(['城市路线卡','全部具体方案'])
        self.sort = QComboBox();self.sort.addItems(['舒适时间优先','少换站','参考费用','总行程时间','卧铺夜数','白天少坐车','车次首晚','证据完整性','偏好匹配'])
        row = QHBoxLayout()
        for title,field in [('短高铁',self.short),('换站',self.changes),('视图',self.view),('排序',self.sort)]:form.addRow(title,field)
        form.addRow(self.known)
        workspace=QHBoxLayout();layout.addLayout(workspace,1)
        filters_scroll=QScrollArea();filters_scroll.setWidgetResizable(True);filters_scroll.setFixedWidth(300)
        filters_body=QWidget();filters_layout=QVBoxLayout(filters_body);filters_layout.addLayout(form)
        filters_scroll.setWidget(filters_body);filters_scroll.setObjectName('filterPanel');workspace.addWidget(filters_scroll)
        content=QVBoxLayout();workspace.addLayout(content,1)
        more=QWidget()
        more_form=QFormLayout(more);more_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows);self.extra={}
        for key,label in [('origin','实际出发站'),('destination','实际到达站'),
                          ('trains','包含车次'),('exclude_trains','排除车次'),('sleeper','卧铺席别')]:
            field=QLineEdit() if key=='sleeper' else NameEdit('train' if key in ('trains','exclude_trains') else 'station',picker=True,
                selection_help='必须包含所选的全部车次' if key=='trains' else '排除含任一所选车次的方案' if key=='exclude_trains' else '任一所选车站匹配即可')
            if key=='sleeper':field.setPlaceholderText('如：硬卧、二等卧')
            self.extra[key]=field;more_form.addRow(label,field)
        for key,label in [('leg_min_minutes','单段最短 / 分钟'),('leg_max_minutes','单段最长 / 分钟（0 不限制）')]:
            field=QSpinBox();field.setRange(0,100000);self.extra[key]=field;more_form.addRow(label,field)
        self.target_only=QCheckBox('只看舒适时间内的行程');more_form.addRow(self.target_only)
        self.empty=QComboBox();self.empty.addItems(['空夜不限','没有空夜','存在空夜']);more_form.addRow('空夜',self.empty)
        self.first_night=QComboBox();self.first_night.addItems(['首晚不限（含未知）','车次首晚']);more_form.addRow('车次首晚',self.first_night)
        self.status=QComboBox();self.status.addItems(['核验状态不限','约束合格（资格余票另核）','待核验','已证实违反']);more_form.addRow('核验状态',self.status)
        filters_layout.addWidget(Disclosure('更多筛选',more))
        apply_button=QPushButton('应用筛选');apply_button.setObjectName('primary');apply_button.clicked.connect(self.apply)
        filters_layout.addWidget(apply_button);filters_layout.addStretch()
        buttons = QHBoxLayout()
        for text,fn in [('查看行程',self.detail),('导出',self.export)]:
            button=QPushButton(text);button.clicked.connect(fn);buttons.addWidget(button)
            if text=='核价本页':button.setToolTip('路线卡视图只查询本页各组的代表方案；具体方案视图查询本页方案，已查区间会复用。')
        extra_actions=QToolButton();extra_actions.setObjectName('actionMenu');extra_actions.setText('更多操作');extra_actions.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(extra_actions)
        for text,fn in [('收藏 / 取消收藏',self.favorite),('比较选中方案',self.compare),('检索详情',self.audit),('删除所选',self.remove)]:menu.addAction(text,fn)
        extra_actions.setMenu(menu);buttons.addWidget(extra_actions);buttons.addStretch()
        content.addLayout(buttons)
        self.table=QTableWidget(0,5)
        self.table.setHorizontalHeaderLabels(['城市顺序','卧铺夜数','方案数 / 车次','换站','票价 / 核验'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemDoubleClicked.connect(self.detail)
        self.table.horizontalHeader().setStretchLastSection(True)
        content.addWidget(self.table,1)
        self.cards=QListWidget()
        self.cards.setStyleSheet('QListWidget {background:transparent;color:#19323b;border:0;padding:0;} QListWidget::item {background:transparent;border:1px solid transparent;border-radius:10px;margin:5px 0;padding:2px;} QListWidget::item:selected {border-color:#27917b;}')
        self.cards.itemDoubleClicked.connect(self.detail)
        content.addWidget(self.cards,1)
        page_row=QHBoxLayout()
        previous=QPushButton('上一页');previous.clicked.connect(lambda:self.move(-1))
        next_button=QPushButton('下一页');next_button.clicked.connect(lambda:self.move(1))
        self.count=QLabel()
        for item in (previous,self.count,next_button):page_row.addWidget(item)
        content.addLayout(page_row)

    def load(self, run_id):
        self.attempted_fare_sync=None
        if self.run_id!=run_id:
            for field in (self.cities,self.exclude):field.clear()
            for field in (self.short,self.changes,self.empty,self.first_night,self.status):field.setCurrentIndex(0)
            self.known.setChecked(False);self.target_only.setChecked(False)
            for field in self.extra.values():
                if isinstance(field,QLineEdit):field.clear()
                else:field.setValue(0)
        self.run_id=run_id
        records=self.main.store.rows('SELECT * FROM runs WHERE id=?',(run_id,))
        if not records:self.clear('该检索任务已删除');return
        self.run=records[0]
        self.request=json.loads(self.run['request'])
        self.catalog=NameCatalog.load(self.main.store,self.run['snapshot'],json.loads(self.run['checkpoint']).get('mappings',{}))
        for field in [self.cities,self.exclude,*self.extra.values()]:
            if isinstance(field,NameEdit):field.set_catalog(self.catalog)
        self.minimum.setValue(self.request['min_nights']);self.maximum.setValue(self.request['max_nights'])
        self.budget.setValue(self.request['budget'])
        self.page=0
        self.refresh()
        QTimer.singleShot(0,self.sync_cached_fares)

    def sync_cached_fares(self):
        if not self.run_id or self.main.closing or getattr(self.main,'suppress_fare_sync',False) or self.main.worker.running:return
        if self.main.pages.currentWidget() is not self:return
        from ..fares import fare_signature
        signature=fare_signature(self.main.store,self.run['snapshot'],self.run_id)
        attempt=(self.run_id,signature)
        if not signature[1] or self.attempted_fare_sync==attempt or self.main.store.setting('fare_sync:'+self.run_id)==signature:return
        self.attempted_fare_sync=attempt
        self.main.start_job('sync_fares',lambda _:self.refresh(),run_id=self.run_id)

    def filters(self):
        split=lambda text:[x.strip() for x in text.replace('，',',').split(',') if x.strip()]
        mode=self.city_mode.currentText()
        f={'min_nights':self.minimum.value(),'max_nights':self.maximum.value(),
           'budget':self.budget.value(),'known_price':self.known.isChecked(),
           'favorite':self.favorites,'exclude_cities':self.exclude.values()}
        f['ordered_cities' if mode=='顺序' else 'cities']=self.cities.values()
        f['city_mode']=mode
        if self.short.currentIndex():f['short_hsr']=self.short.currentIndex()-1
        if self.changes.currentIndex():f['max_changes']=self.changes.currentIndex()-1
        for key,field in self.extra.items():
            if isinstance(field,NameEdit) and field.text().strip():
                f[key]=field.values() if field.multiple else field.value()
                if key in ('origin','destination') and isinstance(f[key],list) and len(f[key])==1:f[key]=f[key][0]
            elif isinstance(field,QLineEdit) and field.text().strip():
                f[key]=split(field.text()) if key in ('trains','exclude_trains') else field.text().strip()
            elif isinstance(field,QSpinBox) and field.value():f[key]=field.value()
        f['comfort_only']=self.target_only.isChecked()
        f['daylight_minutes']=self.request['daylight_end']-self.request['daylight_start']
        if self.empty.currentIndex():f['empty_nights']=self.empty.currentIndex()==2
        if self.first_night.currentIndex():f['first_night']=1
        if self.status.currentIndex():f['status']=['','passed','pending','failed'][self.status.currentIndex()]
        validate_filters(self.main.store,self.run_id,f)
        return f

    def apply(self):
        if not self.run_id:return
        if self.maximum.value()>self.request['max_nights'] or self.minimum.value()<self.request['min_nights'] or self.budget.value()>self.request['budget']:
            self.main.message('此范围未计算。请新建检索任务，不能从当前目录推断没有路线。')
            return
        try:
            self.filters()
            self.page=0;self.refresh()
        except ValueError as exc:self.main.message(str(exc))

    def choose_favorite_task(self,index):
        if index>=0 and self.favorite_tasks.itemData(index):self.load(self.favorite_tasks.itemData(index))

    def clear(self,message='请选择检索任务'):
        self.run_id=None;self.run=None;self.request=None;self.rows=[];self.total=0;self.page=0
        self.table.clearContents();self.table.setRowCount(0);self.cards.clear()
        self.table.setVisible(self.view.currentIndex()!=0);self.cards.setVisible(self.view.currentIndex()==0)
        self.info.setText(message);self.info_help.set_help(message);self.info.setToolTip('');self.count.setText('暂无方案')
        if self.favorites:
            self.favorite_tasks.blockSignals(True);self.favorite_tasks.clear();self.favorite_tasks.blockSignals(False)

    def refresh(self):
        if self.favorites:
            runs=self.main.store.rows('SELECT DISTINCT r.run_id,x.request FROM results r JOIN favorites f ON f.result_id=r.id JOIN runs x ON x.id=r.run_id ORDER BY r.run_id')
            self.favorite_tasks.blockSignals(True);self.favorite_tasks.clear()
            for run in runs:
                req=json.loads(run['request']);self.favorite_tasks.addItem(req['origin']+' → '+req['destination']+' · '+run['run_id'][:8],run['run_id'])
            index=self.favorite_tasks.findData(self.run_id)
            self.favorite_tasks.setCurrentIndex(max(0,index));self.favorite_tasks.blockSignals(False)
            if not runs:self.clear('暂无收藏');return
            if runs and self.run_id not in {r['run_id'] for r in runs}:self.load(runs[0]['run_id']);return
        if not self.run_id:
            if self.favorites:
                runs=self.main.store.rows('SELECT DISTINCT r.run_id FROM results r JOIN favorites f ON f.result_id=r.id ORDER BY r.run_id')
                if runs:self.load(runs[0]['run_id'])
            return
        records=self.main.store.rows('SELECT * FROM runs WHERE id=?',(self.run_id,))
        if not records:self.clear('该检索任务已删除');return
        self.run=records[0]
        self.grouped=self.view.currentIndex()==0
        sort=['comfort','changes','price','duration','nights','daylight','first_night','evidence','preference'][self.sort.currentIndex()]
        try:
            self.rows,self.total,total_variants=query(self.main.store,self.run_id,self.filters(),self.page,30,self.grouped,sort)
        except ValueError as exc:
            self.rows=[];self.total=0;self.table.setRowCount(0);self.cards.clear()
            self.info.setText(str(exc));self.count.setText('范围未计算或筛选参数无效')
            return
        if self.page and not self.rows:
            self.page=max(0,(self.total-1)//30);self.refresh();return
        status='枚举已完成（仅声明的快照范围）' if self.run['status']=='completed' else '尚未完成，以下为部分结果'
        self.info.setText(f"{self.request['origin']} → {self.request['destination']} · {self.main.store.snapshot(self.run['snapshot'])['tag']} · 出发 {self.request.get('departure_date') or '全部适用日期'} · {status}\n卧铺、票价、物理区段等证据缺失的候选属于待核验，不是全部条件合格。")
        self.info.setText(self.info.text()+'\n'+daylight_rule_text(self.request))
        self.info.setText(self.info.text()+'\n'+time_windows_text(self.request))
        if outdated_search_rules(self.request):
            self.info.setText('历史任务保留原始结果；旧舒适度索引可能未计首末程，请新建任务获得准确筛选和推荐。\n' + self.info.text())
        details=self.info.text()
        if self.run['status']=='limited':details+='\n受限停止：'+json.loads(self.run['audit']).get('resource_stop','达到资源保护上限')+'；结果不完整。'
        self.info_help.set_help(details);self.info.setToolTip(details)
        origin=self.catalog.station_name(self.request['origin']) if self.request.get('origin_kind')=='station' else self.request['origin']
        destination=self.catalog.station_name(self.request['destination']) if self.request.get('destination_kind')=='station' else self.request['destination']
        state='已完成' if self.run['status']=='completed' else '部分结果'
        if self.run['status']=='limited':state='受限停止 · 结果不完整'
        if outdated_search_rules(self.request):state+=' · 历史规则'
        self.info.setText(f"{origin} → {destination}   ·   {self.request.get('departure_date') or '全部日期'}   ·   {state}   ·   待核验")
        self.table.setVisible(not self.grouped);self.cards.setVisible(self.grouped);self.cards.clear()
        self.table.setHorizontalHeaderLabels(['城市顺序','卧铺夜数','方案数 / 车次','换站','票价 / 核验'])
        self.table.setRowCount(len(self.rows))
        for i,row in enumerate(self.rows):
            if self.grouped:
                values=[' → '.join(json.loads(row['pattern'])),f"{row['min_nights']}—{row['max_nights']}",str(row['variants']),f"{row['min_changes']}—{row['max_changes']}",'未知 · 待核验']
            else:
                p=json.loads(row['payload'])
                values=[' → '.join(p['pattern']),str(row['nights']),' / '.join(l['train'] for l in p['legs']),str(row['changes']),comfort_text(p,self.request)+'\n'+('未知' if row['price'] is None else str(row['price']))+' · '+{'passed':'约束合格','pending':'待核验','failed':'已证实违反'}[row['status']]]
            if self.grouped:
                representative=self.main.store.rows('SELECT payload FROM results WHERE id=?',(row['representative'],))[0]
                p=json.loads(representative['payload'])
                card=RouteCard(p,row,self.request)
                item=QListWidgetItem();item.setSizeHint(QSize(560,260));self.cards.addItem(item);self.cards.setItemWidget(item,card)
            for col,value in enumerate(values):self.table.setItem(i,col,QTableWidgetItem(value))
        self.table.resizeColumnsToContents()
        pages=(self.total+29)//30
        self.count.setText(f'{self.page+1 if pages else 0} / {pages} 页 · 匹配 {self.total} '+('个城市组合' if self.grouped else '个方案')+f' / {total_variants} 个具体方案')

    def price_page(self):
        if not self.run_id or not self.rows:return
        if not self.request.get('departure_date'):
            self.main.message('批量核价请使用所选日期任务。全部日期任务可在完整行程里选择具体日期逐条核价。');return
        ids=[r['representative'] if self.grouped else r['id'] for r in self.rows]
        self.main.start_job('fare_batch',lambda _:self.refresh(),result_ids=ids,date=self.request['departure_date'])

    def move(self, delta):
        if self.run_id and 0<=self.page+delta<max(1,(self.total+29)//30):
            try:
                self.filters()
                self.page+=delta;self.refresh()
            except ValueError as exc:self.main.message(str(exc))

    def selected(self):
        rows=[self.cards.currentRow()] if self.grouped and self.cards.currentRow()>=0 else sorted({item.row() for item in self.table.selectedItems()})
        if not rows:raise ValueError('请先选择路线或具体方案')
        return [self.rows[i] for i in rows]

    def payload(self,row):
        identity=row['representative'] if self.grouped else row['id']
        records=self.main.store.rows('SELECT payload FROM results WHERE id=?',(identity,))
        if not records:raise ValueError('此方案已删除，请刷新目录')
        return json.loads(records[0]['payload'])

    def remove(self):
        try:
            rows=self.selected()
            self.main.delete_catalog('patterns' if self.grouped else 'results',[self.run_id],
                result_ids=[] if self.grouped else [row['id'] for row in rows],patterns=[row['pattern'] for row in rows] if self.grouped else [])
        except ValueError as exc:self.main.message(str(exc))

    def show_text(self,title,text):
        dialog=QDialog(self);dialog.setWindowTitle(title);dialog.resize(950,720)
        layout=QVBoxLayout(dialog);edit=QTextEdit();edit.setReadOnly(True);edit.setPlainText(text);layout.addWidget(edit)
        if self.run_id:self.main.watch_dialog(dialog,self.run_id)
        dialog.exec()

    def itinerary(self,payload):
        request=self.request if self.run_id==payload['run_id'] else json.loads(self.main.store.rows('SELECT request FROM runs WHERE id=?',(payload['run_id'],))[0]['request'])
        viewer=ItineraryView(payload,dict(request,budget=self.budget.value()),read_assessment(self.main.store,payload['id']))
        def fetch(day):
            def done(assessment):
                from shiboken6 import isValid
                if isValid(viewer):viewer.set_assessment(assessment)
                self.refresh()
            self.main.start_job('fare',done,result_id=payload['id'],date=day)
        viewer.quote_requested.connect(fetch)
        viewer.delete_requested.connect(lambda identity:self.main.delete_catalog('results',[payload['run_id']],[identity]))
        return viewer

    def detail(self,*args):
        try:
            row=self.selected()[0]
            if not self.grouped:
                dialog=QDialog(self);dialog.setWindowTitle('行程时间轴与票价');dialog.resize(1120,840)
                layout=QVBoxLayout(dialog);layout.addWidget(self.itinerary(self.payload(row)))
                self.main.watch_dialog(dialog,self.run_id,[row['id']]);dialog.exec();return
            dialog_run=self.run_id;dialog_filters=dict(self.filters())
            dialog=QDialog(self);dialog.setWindowTitle('完整行程与兼容替代方案');dialog.resize(1050,780)
            layout=QVBoxLayout(dialog)
            recommendation=QComboBox();recommendation.addItems(['舒适时间优先','少换站','费用优先','证据较完整','偏好匹配','全部方案'])
            layout.addWidget(recommendation)
            info=QLabel('每次切换均选择整条已校验搭配，不独立拼接不同方案的车站。');layout.addWidget(info)
            selector=QComboBox();layout.addWidget(selector)
            view_area=QVBoxLayout();layout.addLayout(view_area,1)
            page=[0];variants=[[]]
            def load_variants():
                filters=dict(dialog_filters);filters['pattern']=row['pattern']
                variants[0],count,_=query(self.main.store,dialog_run,filters,page[0],100,False,['comfort','changes','price','evidence','preference','comfort'][recommendation.currentIndex()])
                if not count:dialog.reject();return
                if not variants[0]:page[0]=max(0,(count-1)//100);load_variants();return
                selector.clear()
                for variant in variants[0]:
                    p=json.loads(variant['payload'])
                    selector.addItem(' / '.join(f"{l['train']} {l['from_name']}→{l['to_name']}" for l in p['legs'])+' · '+variant['id'][:8])
                info.setText(f'兼容替代搭配，共 {count} 条；每页 100 条；{page[0]+1} / {(count+99)//100} 页。切换整条有效路线。')
            def display(index):
                while view_area.count():
                    old=view_area.takeAt(0).widget();old.deleteLater()
                if index>=0 and index<len(variants[0]):view_area.addWidget(self.itinerary(json.loads(variants[0][index]['payload'])))
            selector.currentIndexChanged.connect(display)
            recommendation.currentIndexChanged.connect(lambda _:load_variants())
            buttons=QHBoxLayout()
            def move(delta):
                filters=dict(dialog_filters);filters['pattern']=row['pattern'];_,count,_=query(self.main.store,dialog_run,filters,0,1)
                if 0<=page[0]+delta<max(1,(count+99)//100):page[0]+=delta;load_variants()
            for label,delta in [('上一页',-1),('下一页',1)]:
                button=QPushButton(label);button.clicked.connect(lambda checked=False,d=delta:move(d));buttons.addWidget(button)
            favorite=QPushButton('收藏当前具体方案')
            def save():
                idx=selector.currentIndex()
                if idx>=0:
                    with self.main.store.connect() as db:db.execute('INSERT OR IGNORE INTO favorites VALUES(?,?)',(variants[0][idx]['id'],now()))
                    self.main.sync_catalog(dict(run_ids=[dialog_run]))
            favorite.clicked.connect(save);buttons.addWidget(favorite);layout.addLayout(buttons)
            self.main.watch_dialog(dialog,dialog_run,refresh=load_variants)
            load_variants();dialog.exec()
        except Exception as exc:self.main.message(str(exc))

    def favorite(self):
        try:
            rows=self.selected()
            if self.grouped:raise ValueError('请打开行程选择具体方案收藏，或切换“全部具体方案”。')
            with self.main.store.connect() as db:
                for row in rows:
                    if db.execute('SELECT 1 FROM favorites WHERE result_id=?',(row['id'],)).fetchone():db.execute('DELETE FROM favorites WHERE result_id=?',(row['id'],))
                    else:db.execute('INSERT INTO favorites VALUES(?,?)',(row['id'],now()))
            self.main.sync_catalog(dict(run_ids=[self.run_id]))
        except Exception as exc:self.main.message(str(exc))

    def compare(self):
        try:
            rows=self.selected()
            if self.grouped:raise ValueError('请切换全部具体方案，再选择要比较的方案。')
            dialog=QDialog(self);dialog.setWindowTitle('方案比较');dialog.resize(1100,760)
            layout=QVBoxLayout(dialog);tabs=QTabWidget();layout.addWidget(tabs)
            table=QTableWidget(7,len(rows));table.setVerticalHeaderLabels(['卧铺夜数','换站次数','最少白昼离车 / 分钟','学生参考价范围','短高铁','空夜','核验状态'])
            table.setHorizontalHeaderLabels([r['id'][:8] for r in rows])
            for col,row in enumerate(rows):
                p=self.payload(row)
                values=[p['nights'],p['changes'],p.get('min_daylight_off_minutes','未知'),p.get('price_range') or '未知',p['short_hsr'],p['empty_nights'] or '无',p['status']]
                for r,value in enumerate(values):table.setItem(r,col,QTableWidgetItem(str(value)))
                tabs.addTab(self.itinerary(p),row['id'][:8])
            tabs.insertTab(0,table,'并排比较');tabs.setCurrentIndex(0);table.resizeColumnsToContents()
            self.main.watch_dialog(dialog,self.run_id,[row['id'] for row in rows]);dialog.exec()
        except Exception as exc:self.main.message(str(exc))

    def audit(self):
        if self.run_id:self.show_text('完整性审计',json.dumps(json.loads(self.run['audit']),ensure_ascii=False,indent=2))

    def export(self):
        if not self.run_id:return
        from PySide6.QtWidgets import QInputDialog
        scope,ok=QInputDialog.getItem(self,'导出范围','选择导出范围',['当前筛选','任务全部','选中具体方案'],0,False)
        if not ok:return
        try:
            filters={} if scope=='任务全部' else self.filters()
            if scope=='选中具体方案':
                if self.grouped:raise ValueError('选中导出需要切换全部具体方案')
                filters={'ids':[r['id'] for r in self.selected()]}
            _,_,count=query(self.main.store,self.run_id,filters,0,1,False)
            if count>=10_000:
                from PySide6.QtWidgets import QMessageBox
                answer=QMessageBox.warning(self,'大批量导出',f'将导出 {count:,} 条完整方案、逐段信息与证据，可能产生较大文件并耗时较长。建议先筛选或选择少量方案。\n实际大小取决于行程和证据；磁盘不足 5 GiB 时会停止并清理未完成导出。\n仍然导出？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
                if answer!=QMessageBox.StandardButton.Yes:return
            path=QFileDialog.getExistingDirectory(self,'选择导出父目录')
            if path:self.main.start_job('export',lambda result:self.main.message('已导出：'+result),run_id=self.run_id,path=path,filters=filters,scope=scope)
        except Exception as exc:self.main.message(str(exc))
