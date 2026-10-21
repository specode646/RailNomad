import json
import sqlite3
from PySide6.QtCore import QTime,Qt
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGroupBox,
    QLabel, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit, QTimeEdit,
    QPushButton, QScrollArea, QMessageBox,QGridLayout,QAbstractSpinBox)
from ..domain.model import Request,daylight_rule_text,time_windows_text
from ..domain.search import new_run, available_departure_dates
from .places import PlacePicker
from .duration import DaytimeLimit
from .names import NameEdit,NameCatalog
from .widgets import field_label,HelpButton,Disclosure


class PlannerPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.fields = {}
        layout = QVBoxLayout(self)
        title = QLabel('规划一趟夜车旅行')
        title.setObjectName('heading')
        layout.addWidget(title)
        self.snapshot_label = QLabel()
        self.snapshot_label.setObjectName('muted');layout.addWidget(self.snapshot_label)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        contents = QWidget()
        box = QVBoxLayout(contents)
        base = QGroupBox('行程')
        form = QFormLayout(base)
        preferences=QGroupBox('路线偏好与限定站点')
        preferences_form=QFormLayout(preferences)
        self.origin = PlacePicker()
        self.destination = PlacePicker()
        form.addRow('出发地', self.origin)
        form.addRow('目的地', self.destination)
        self.departure_date=QComboBox()
        self.departure_date.addItem('全部适用日期',None)
        form.addRow('出发日期',self.departure_date)
        self.mode = QComboBox()
        self.mode.addItems(['最少—最多晚数', '恰好 N 晚'])
        self.mode.setFixedWidth(170)
        nights = QHBoxLayout()
        nights.addWidget(self.mode)
        self.add_number(nights, 'min_nights', 3, 1, 2147483647, '最少')
        self.add_number(nights, 'max_nights', 4, 1, 2147483647, '最多')
        self.fields['min_nights'].setFixedWidth(80);self.fields['max_nights'].setFixedWidth(80);nights.addStretch()
        form.addRow('卧铺夜数', nights)
        self.night_warning=QLabel();self.night_warning.setWordWrap(True);self.night_warning.setStyleSheet('color:#a36a22;')
        self.night_warning.setVisible(False);form.addRow(self.night_warning)
        self.mode.currentIndexChanged.connect(lambda i: self.fields['max_nights'].setEnabled(i == 0))
        budget = QDoubleSpinBox()
        budget.setRange(0, 1000000)
        budget.setValue(1000)
        budget.setPrefix('¥ ')
        budget.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.fields['budget'] = budget
        form.addRow(field_label('学生预算','按学生参考价筛选；缺价方案保留待核价，已知费用下界超预算时排除。'), budget)
        for key, label in [('required_cities','必须停留城市'), ('forbidden_cities','禁止经过城市'),
                           ('preferred_cities','优先停留城市'), ('preferred_stations','优先车站'),
                           ('allowed_stations','限定上下车站'), ('other_preferences','其他偏好')]:
            field = QLineEdit() if key=='other_preferences' else NameEdit('station' if key.endswith('stations') else 'city')
            if key=='other_preferences':field.setPlaceholderText('记录你的其他偏好')
            self.fields[key] = field
            if key=='required_cities':
                self.required_mode=QComboBox()
                self.required_mode.addItem('全部 AND','AND')
                self.required_mode.addItem('任一 OR','OR')
                row=QHBoxLayout();row.addWidget(field,1);row.addWidget(self.required_mode)
                form.addRow(label,row)
            else:
                explanation={'forbidden_cities':'禁止经过这些城市，包括乘车途中的经停城市。','preferred_cities':'只影响推荐顺序，不裁剪可行路线。','preferred_stations':'填写中文站名或代码，只影响推荐顺序。','allowed_stations':'只允许在填写的车站上下车，包括起终点。可填中文站名或代码，留空不限制。','other_preferences':'仅保存这段文字，不会自动解析成检索条件。'}[key]
                (form if key=='forbidden_cities' else preferences_form).addRow(field_label(label,explanation), field)
        consecutive = QCheckBox('连续卧铺夜')
        consecutive.setChecked(True)
        self.fields['consecutive'] = consecutive
        form.addRow(field_label('夜晚安排','连续模式要求整个行程没有未被卧铺覆盖的空夜。'),consecutive)
        box.addWidget(base)
        schedule=QGroupBox('到发时间')
        schedule_form=QGridLayout(schedule)
        schedule_form.setAlignment(Qt.AlignmentFlag.AlignTop)
        schedule_form.addWidget(field_label('','舒适范围内优先推荐；超出舒适但在极限内的方案保留并标注。首趟可早发、末趟可晚到，最早到站仍生效。发车结束时间可跨午夜。'),0,0)
        schedule_form.addWidget(QLabel('舒适'),0,1);schedule_form.addWidget(QLabel('极限'),0,2)
        for row,text in [(1,'到站'),(2,'发车')]:schedule_form.addWidget(QLabel(text),row,0)
        defaults=Request('a','b')
        for label,keys in [('舒适到站',('target_arrive_start','target_arrive_end')),('极限到站',('arrive_start','arrive_end')),('舒适发车',('target_depart_start','target_depart_end')),('极限发车',('depart_start','depart_end'))]:
            row=QHBoxLayout()
            for i,key in enumerate(keys):
                if i:row.addWidget(QLabel('至'))
                value=getattr(defaults,key);field=QTimeEdit(QTime(value//60,value%60));field.setDisplayFormat('HH:mm')
                field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
                self.fields[key]=field;row.addWidget(field)
            schedule_form.addLayout(row,1 if '到站' in label else 2,1 if '舒适' in label else 2)
        box.addWidget(schedule)
        advanced = QGroupBox('更多设置')
        advanced_form = QFormLayout(advanced)
        advanced_form.addRow(preferences)
        self.short_settings=QWidget();short_form=QFormLayout(self.short_settings);short_form.setContentsMargins(0,0,0,0)
        for key, label in [('min_sleeper_minutes','最短卧铺乘车 / 分钟'), ('same_station_gap','夜车之间同站停留 / 分钟'),
                           ('same_city_gap','夜车之间同城换站停留 / 分钟'),
                           ('short_same_station_gap','短高铁衔接同站换乘 / 分钟'),
                           ('short_same_city_gap','短高铁衔接同城换站 / 分钟'),
                           ('short_hsr_max','单段短高铁最长 / 分钟')]:
            field = QSpinBox()
            field.setRange(0 if key != 'min_sleeper_minutes' else 1, 100000)
            if key=='short_hsr_max':field.setRange(1,60)
            field.setValue(getattr(defaults,key))
            self.fields[key] = field
            if key=='min_sleeper_minutes':
                field.setSuffix(' 分钟')
                form.addRow(field_label('最短卧铺乘车','每段实际乘坐的卧铺区间至少达到此时长，默认 450 分钟（7 小时 30 分钟）。'),field)
            elif key.startswith('short_'):short_form.addRow(label,field)
            else:advanced_form.addRow(label,field)
        daytime=QGroupBox('白天最多坐车多久')
        daytime_form=QFormLayout(daytime)
        daylight_max=DaytimeLimit()
        daylight_max.setValue(defaults.max_daylight_on_train)
        self.fields['max_daylight_on_train']=daylight_max
        daytime_form.addRow(field_label('旅途中每天','每天所有车段在白天窗口中的时长合计；白天默认 07:00—20:00。出发当天和抵达当天另设上限，其余日期适用此处设置。'),daylight_max)
        for key,label in [('first_daylight_limit','出发当天'),('last_daylight_limit','抵达当天')]:
            field=DaytimeLimit(daylight_max)
            field.setValue(getattr(defaults,key))
            self.fields[key]=field;daytime_form.addRow(field_label(label,'只作用于实际起止当天，跨日长途中的其他日期不豁免。同一天出发并抵达时，同时满足两项有值上限。'),field)
        box.removeWidget(schedule)
        timing_row=QHBoxLayout();timing_row.setSpacing(14);timing_row.addWidget(schedule,3);timing_row.addWidget(daytime,2)
        box.addLayout(timing_row)
        for key, label in [('daylight_start','白天从几点算起'), ('daylight_end','白天算到几点')]:
            value = getattr(defaults,key)
            field = QTimeEdit(QTime(value//60,value%60))
            field.setDisplayFormat('HH:mm')
            field.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            self.fields[key] = field
            advanced_form.addRow(label,field)
        short = QCheckBox('允许一次短高铁')
        short.setChecked(defaults.allow_short_hsr)
        self.fields['allow_short_hsr'] = short
        def update_night_warning(*_):
            maximum=self.fields['min_nights' if self.mode.currentIndex()==1 else 'max_nights'].value()
            threshold=4 if short.isChecked() else 6
            self.night_warning.setText(f"{'含短高铁超过 4 晚' if short.isChecked() else '不含短高铁超过 6 晚'}，计算量和存储量可能快速增长；建议减少夜数或限定城市、车站。")
            self.night_warning.setVisible(maximum>threshold)
        for key in ('min_nights','max_nights'):self.fields[key].valueChanged.connect(update_night_warning)
        self.mode.currentIndexChanged.connect(update_night_warning);short.toggled.connect(update_night_warning)
        form.addRow(field_label('短高铁','高开销选项，默认关闭。最多一次且最长 60 分钟；整段在舒适时间窗口内，衔接同站 30 / 换站 90 分钟。建议选择单日、连续夜和较少夜数。最多保存 30 万条，每次运行最多 10 分钟。'), short)
        warning=QLabel('开启短高铁会明显增加计算量与存储量');warning.setWordWrap(True);short_form.addRow(warning)
        form.addRow(self.short_settings);self.short_settings.setVisible(False)
        short.toggled.connect(self.short_settings.setVisible)
        protection=QLabel('所有检索最多保存 30 万条；触顶后保留部分结果。');protection.setWordWrap(True)
        advanced_form.addRow(field_label('资源保护','普通检索每次运行 20 分钟；开启短高铁每次运行 10 分钟。目录库及日志上限 8 GiB，磁盘至少保留 5 GiB。达到保护阈值会停止，并明确标为结果不完整。'),protection)
        box.addWidget(Disclosure('更多设置 · 换乘与偏好',advanced))
        box.addStretch()
        scroll.setWidget(contents)
        layout.addWidget(scroll,1)
        start = QPushButton('开始检索');start.setObjectName('primary')
        start.clicked.connect(self.submit)
        layout.addWidget(start)
        self.refresh_places()

    def add_number(self, layout, key, default, minimum, maximum, label):
        field = QSpinBox()
        field.setRange(minimum,maximum)
        field.setValue(default)
        layout.addWidget(QLabel(label))
        layout.addWidget(field)
        self.fields[key] = field

    def refresh_places(self):
        try:
            snap = self.main.store.snapshot()
            previous=self.departure_date.currentData()
            same_snapshot=getattr(self,'date_snapshot',None)==snap['id']
            self.departure_date.clear()
            self.departure_date.addItem('全部适用日期',None)
            for day in available_departure_dates(snap):
                self.departure_date.addItem(day,day)
            index=self.departure_date.findData(previous) if same_snapshot else -1
            self.departure_date.setCurrentIndex(index if index>=0 else min(1,self.departure_date.count()-1))
            self.date_snapshot=snap['id']
            quality = json.loads(snap['quality'])
            self.snapshot_label.setText(f"当前快照：{snap['tag']} · 日历 {' — '.join(quality['calendar'])} · 隔离 {len(quality.get('quarantined_trips',[]))} 趟异常服务 · 原始城市归属未知 {quality['unknown_cities']} 站")
            db = sqlite3.connect(snap['path'])
            stops = list(db.execute('SELECT id,name,city FROM stops ORDER BY name,id'))
            db.close()
            mappings = self.main.store.setting('city_mapping:' + snap['id'], {})
            from collections import Counter
            resolved = [(sid, name, mappings.get(sid) or city) for sid, name, city in stops]
            unknown=sum(not city for sid,name,city in resolved)
            timing=(f"修复 {len(quality.get('repaired_trips',[]))} / 局部异常 {len(quality.get('time_affected_trips',[]))} 趟（按上下车区间处理）"
                    if quality.get('timetable_policy')=='segment_endpoints_v1' else f"修复 {len(quality.get('repaired_trips',[]))} / 隔离 {len(quality.get('quarantined_trips',[]))} 趟")
            self.snapshot_label.setText(f"当前快照：{snap['tag']} · 日历 {' — '.join(quality['calendar'])} · {timing} · 城市归属 {len(resolved)-unknown} 站已知 / {unknown} 站未知")
            self.snapshot_label.setToolTip(self.snapshot_label.text())
            self.snapshot_label.setText(f"运行图可用 · {' — '.join(quality['calendar'])}"+(' · 含待核验数据' if quality.get('coverage_complete') is False else ''))
            counts = Counter(city for sid, name, city in resolved if city)
            cities = sorted(counts.items())
            for picker in (self.origin, self.destination):
                picker.set_places(cities, resolved)
            self.catalog=NameCatalog.load(self.main.store,snap['id'],mappings)
            for field in self.fields.values():
                if isinstance(field,NameEdit):field.set_catalog(self.catalog)
        except ValueError:
            self.snapshot_label.setText('尚无启用快照，请先在数据中心下载或导入。')
            for picker in (self.origin, self.destination):
                picker.set_places([], [])
            self.catalog=NameCatalog()
            for field in self.fields.values():
                if isinstance(field,NameEdit):field.set_catalog(self.catalog)
            self.departure_date.clear()
            self.departure_date.addItem('暂无可用运行图',None)

    def submit(self):
        try:
            snap = self.main.store.snapshot()
            data = {}
            data['required_city_mode']=self.required_mode.currentData()
            data['departure_date']=self.departure_date.currentData()
            for key, field in self.fields.items():
                if isinstance(field, QTimeEdit):
                    data[key] = field.time().hour()*60 + field.time().minute()
                elif isinstance(field,QCheckBox):
                    data[key] = field.isChecked()
                elif isinstance(field,NameEdit):
                    data[key]=field.values()
                elif isinstance(field,QLineEdit):
                    data[key] = field.text() if key == 'other_preferences' else [v.strip() for v in field.text().replace('，',',').split(',') if v.strip()]
                else:
                    data[key] = field.value()
            data['limit_daylight']=any(data[key] is not None for key in ('max_daylight_on_train','first_daylight_limit','last_daylight_limit'))
            if data['limit_daylight'] and data['max_daylight_on_train'] is None:
                data['max_daylight_on_train']=data['daylight_end']-data['daylight_start']
            if self.mode.currentIndex() == 1:
                data['max_nights'] = data['min_nights']
            for key,combo in [('origin',self.origin), ('destination',self.destination)]:
                chosen = combo.selected_place()
                if not chosen:
                    raise ValueError('请从列表选择明确的城市或车站')
                data[key+'_kind'],data[key] = chosen
            request = Request(**data).validate()
            from ..resources import validate_request,description,risk_warnings
            validate_request(request)
            summary = (f"{self.origin.currentText()} → {self.destination.currentText()}\n"
                       f"出发日期：{request.departure_date or '全部适用日期'}\n"
                       f"卧铺 {request.min_nights}—{request.max_nights} 晚 · {'连续' if request.consecutive else '允许空夜'}\n"
                       f"必须停留城市：{'、'.join(request.required_cities) or '不限'}（{request.required_city_mode}）\n"
                       f"舒适时间优先推荐；超出舒适时间会标注，极限时间决定是否入选。\n"
                       f"{time_windows_text(request.to_dict())}\n"
                       f"{daylight_rule_text(request.to_dict())}\n"
                       f"参考预算 ¥{request.budget:g} · 价格缺失仍保留待核验\n快照 {snap['tag']}\n\n完整参数：\n" +
                       json.dumps(request.to_dict(),ensure_ascii=False,indent=2))
            dialog = QMessageBox(self)
            dialog.setWindowTitle('确认本次检索参数')
            dialog.setText(f"{self.origin.currentText()} → {self.destination.currentText()}\n"
                           f"{request.departure_date or '全部适用日期'} · 卧铺 {request.min_nights}—{request.max_nights} 晚 · {'连续' if request.consecutive else '允许空夜'}\n"
                           f"学生预算 ¥{request.budget:g}\n必须停留：{'、'.join(request.required_cities) or '不限'}（{request.required_city_mode}）\n\n"+description(request))
            dialog.setDetailedText(summary)
            dialog.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
            dialog.button(QMessageBox.StandardButton.Ok).setText('开始检索')
            dialog.button(QMessageBox.StandardButton.Cancel).setText('返回修改')
            if risk_warnings(request):
                dialog.setIcon(QMessageBox.Icon.Warning)
                dialog.button(QMessageBox.StandardButton.Ok).setText('了解风险，仍然检索')
                dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
            if dialog.exec() != QMessageBox.StandardButton.Ok:
                return
            # Check worker availability before creating a queued task.
            if self.main.worker.running:
                raise ValueError('已有后台任务，请等待完成或取消')
            identity = new_run(self.main.store,snap['id'],request)
            self.main.run_search(identity)
        except Exception as exc:
            self.main.message(str(exc))
