"""Native journey cards, day strips and structured fare details."""
from PySide6.QtCore import Qt, Signal, QSize
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QWidget,QFrame,QVBoxLayout,QHBoxLayout,QLabel,
    QScrollArea,QComboBox,QPushButton,QTableWidget,QTableWidgetItem,QHeaderView)
from ..domain.model import comfort_text,comfort_minutes,comfort_for_leg,in_wrapped_window,timetable_warning,train_night_text
from .widgets import HelpButton


def label(text, size=13, color='#294d57', bold=False):
    widget=QLabel(str(text));widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setStyleSheet(f'color:{color};font-size:{size}px;font-weight:{600 if bold else 400};background:transparent;')
    return widget


def money(low,high=None):
    if low is None:return '待核价'
    high=low if high is None else high
    return f'¥{low:g}' if low==high else f'¥{low:g}—{high:g}'


def clock(seconds):
    day,seconds=divmod(seconds,86400)
    return f'第 {day+1} 天 {seconds//3600:02d}:{seconds//60%60:02d}'


def duration(seconds):
    minutes=int(seconds)//60
    return f'{minutes//60} 小时 {minutes%60} 分钟' if minutes>=60 else f'{minutes} 分钟'


class DayStrips(QWidget):
    """Each day uses the same 24-hour scale; transit and daylight stay distinct."""
    def __init__(self,payload,request):
        super().__init__();self.legs=payload['legs'];self.request=request
        self.first=self.legs[0]['dep']//86400;self.last=self.legs[-1]['arr']//86400
        self.setMinimumHeight(45+(self.last-self.first+1)*44)
        self.setToolTip('横轴为每天 00:00—24:00。绿色为卧铺乘车，蓝色为短高铁；浅底为白昼窗口，其他时段为离车。')

    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left=80;width=max(20,self.width()-left-20)
        painter.setPen(QColor('#6b7f85'))
        for hour in (0,6,12,18,24):
            x=left+width*hour/24
            painter.drawText(int(x)-13,18,f'{hour:02d}:00')
        for day in range(self.first,self.last+1):
            y=32+(day-self.first)*44
            painter.setPen(QColor('#294d57'));painter.drawText(5,y+17,f'第 {day+1} 天')
            painter.fillRect(left,y,int(width),22,QColor('#e7ecef'))
            a=self.request['daylight_start']/1440;b=self.request['daylight_end']/1440
            painter.fillRect(int(left+width*a),y,int(width*(b-a)),22,QColor('#fff0cf'))
            for leg in self.legs:
                a=max(0,leg['dep']-day*86400);b=min(86400,leg['arr']-day*86400)
                if b>a:
                    painter.fillRect(int(left+width*a/86400),y,max(2,int(width*(b-a)/86400)),22,QColor('#5587bd' if leg['short'] else '#26786c'))
            painter.setPen(QPen(QColor('#c9d5d9'),1))
            for hour in (0,6,12,18,24):
                x=int(left+width*hour/24);painter.drawLine(x,y,x,y+22)


class RouteCard(QFrame):
    def __init__(self,payload,row,request):
        super().__init__();self.setObjectName('routeCard')
        self.setStyleSheet('QFrame#routeCard {background:white;border:1px solid #d4e2dc;border-radius:10px;}')
        layout=QVBoxLayout(self);layout.setContentsMargins(18,14,18,14)
        header=QHBoxLayout();header.addWidget(label(' → '.join(payload['pattern']),18,bold=True),1)
        header.addWidget(label(f"{row['variants']:,} 个方案",14,'#26786c',True));layout.addLayout(header)
        layout.addWidget(label(f"卧铺 {row['min_nights']}—{row['max_nights']} 晚    ·    换站 {row['min_changes']}—{row['max_changes']} 次    ·    "+('含短高铁代表方案' if payload['short_hsr'] else '纯夜车代表方案')))
        layout.addWidget(label(comfort_text(payload,request),13,'#9a6724' if comfort_minutes(payload,request) else '#26786c',True))
        if any(l.get('timetable_issues') for l in payload['legs']):layout.addWidget(label('含中间站时刻异常 · 上下车时刻可用，详情披露',12,'#a34a35',True))
        elif any(l.get('unused_endpoint_time_issues') for l in payload['legs']):layout.addWidget(label('含未使用的到发字段异常 · 不影响本段上下车时刻，详情披露',12,'#9a6724',True))
        chain='   →   '.join(f"{l['train']}  {l['from_name']}—{l['to_name']}" for l in payload['legs'])
        layout.addWidget(label(chain,13,'#4d6870'))
        gaps=[f"{a['to_name']}停留 {duration(b['dep']-a['arr'])}" for a,b in zip(payload['legs'],payload['legs'][1:])]
        if gaps:layout.addWidget(label('  ·  '.join(gaps),12,'#6b7f85'))
        layout.addWidget(label(f"学生参考 {money(row.get('price'))}    ·    {row['unpriced_count']:,} 条未完整核价    ·    双击查看时间轴与票价",12,'#26786c'))


class ItineraryView(QScrollArea):
    quote_requested=Signal(str)
    delete_requested=Signal(str)

    def __init__(self,payload,request,assessment=None):
        super().__init__();self.setWidgetResizable(True)
        self.payload=payload;self.request=request;self.assessment=assessment
        body=QWidget();layout=QVBoxLayout(body);layout.setContentsMargins(18,16,18,18);layout.setSpacing(12)
        heading=QHBoxLayout();heading.addWidget(label(' → '.join(payload['pattern']),23,bold=True),1)
        remove=QPushButton('删除方案');remove.setObjectName('danger');remove.clicked.connect(lambda:self.delete_requested.emit(payload['id']));heading.addWidget(remove);layout.addLayout(heading)
        layout.addWidget(label(comfort_text(payload,request),14,'#9a6724' if comfort_minutes(payload,request) else '#26786c',True))
        from ..domain.model import outdated_search_rules,route_empty_nights,daylight_rule_text
        warnings=[]
        if outdated_search_rules(request):warnings.append('历史任务采用旧规则，请新建任务使用当前规则。')
        if request.get('consecutive',True) and route_empty_nights(payload):warnings.append('此历史行程有空夜，不符合连续卧铺要求。')
        final=payload['legs'][-1]
        if not final['short'] and final['arr']%86400<request.get('arrive_start',360)*60:warnings.append('此历史行程终点早于最早允许到达时间。')
        if warnings:layout.addWidget(label(' '.join(warnings),13,'#a34a35',True))
        summary=QHBoxLayout()
        for title,value in [('卧铺',f"{payload['nights']} 晚"),('全程',duration(payload['legs'][-1]['arr']-payload['legs'][0]['dep'])),('换站',f"{payload['changes']} 次"),('预算上限',money(request['budget']))]:
            tile=QFrame();tile.setStyleSheet('background:#edf5f1;border-radius:8px;')
            box=QVBoxLayout(tile);box.addWidget(label(title,12,'#6b7f85'));box.addWidget(label(value,18,bold=True));summary.addWidget(tile)
        layout.addLayout(summary)
        layout.addWidget(label('每日行程概览',16,bold=True))
        layout.addWidget(label(daylight_rule_text(request),12,'#26786c'))
        layout.addWidget(DayStrips(payload,request))
        legend=QHBoxLayout()
        for text,color in [('■ 卧铺乘车','#26786c'),('■ 短高铁','#5587bd'),('■ 白昼窗口','#9a6724')]:legend.addWidget(label(text,12,color))
        legend.addStretch();legend.addWidget(HelpButton('其余时段表示离车，未扣除市内接驳；白昼离车时长不等于净游览时长。','行程图说明'));layout.addLayout(legend)
        from .results import sleeper_label
        for index,leg in enumerate(payload['legs']):
            if index:
                previous=payload['legs'][index-1]
                layout.addWidget(label(f"◷  {previous['to_name']}  ·  停留 {duration(leg['dep']-previous['arr'])}  ·  "+('同站接续' if previous['to']==leg['from'] else f"换至 {leg['from_name']}，市内接驳另计"),13,'#9a6724'))
            card=QFrame();card.setStyleSheet('QFrame {background:white;border-radius:8px;}')
            box=QVBoxLayout(card)
            box.addWidget(label(f"{index+1:02d}   {leg['train']}   ·   "+('短高铁' if leg['short'] else sleeper_label(payload,index)),16,'#26786c',True))
            box.addWidget(label(train_night_text(leg),13,'#4d6870',True))
            comfort=comfort_for_leg(leg,request)
            details=[]
            early_exception=index==0 and not in_wrapped_window(leg['dep'],request['depart_start'],request['depart_end'])
            late_exception=index==len(payload['legs'])-1 and leg['arr']%86400>request['arrive_end']*60
            if comfort['departure']:details.append(f"发车偏离舒适时间 {comfort['departure']:g} 分钟"+('（第一趟允许早发）' if early_exception else '（仍在极限时间内）'))
            if comfort['arrival']:details.append(f"到站偏离舒适时间 {comfort['arrival']:g} 分钟"+('（最后一趟允许晚到）' if late_exception else '（仍在极限时间内）'))
            if details:box.addWidget(label('；'.join(details),13,'#9a6724',True))
            warning=timetable_warning(leg)
            if warning:box.addWidget(label(warning,13,'#a34a35',True))
            times=QHBoxLayout();departure=label(f"{clock(leg['dep'])}\n{leg['from_name']}",16,bold=True);departure.setToolTip('车站代码：'+leg['from']);times.addWidget(departure,1)
            transit=label(duration(leg['arr']-leg['dep']),12,'#6b7f85');transit.setMinimumWidth(160)
            transit.setAlignment(Qt.AlignmentFlag.AlignCenter);times.addWidget(transit)
            arrival=label(f"{clock(leg['arr'])}\n{leg['to_name']}",16,bold=True);arrival.setToolTip('车站代码：'+leg['to']);times.addWidget(arrival,1);box.addLayout(times)
            layout.addWidget(card)
        layout.addWidget(label('票价明细',17,bold=True))
        controls=QHBoxLayout();self.date=QComboBox();self.date.addItems(payload['dates']);controls.addWidget(label('实际出发日'));controls.addWidget(self.date,1)
        self.fetch=QPushButton('查询票价');self.fetch.setObjectName('primary');self.fetch.setToolTip('查询所选日期票价，复用已查区间，并更新费用与预算筛选。');self.fetch.clicked.connect(lambda:self.quote_requested.emit(self.date.currentText()));self.fetch.hide();layout.addLayout(controls)
        self.price_summary=label('',14,bold=True);layout.addWidget(self.price_summary)
        self.price_table=QTableWidget(0,6);self.price_table.setHorizontalHeaderLabels(['车次 / 登车日','区间','席别 / 铺位','成人参考','学生估算','来源 / 计算依据'])
        self.price_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.price_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.price_table.horizontalHeader().setStretchLastSection(True)
        self.price_table.setMinimumHeight(210);layout.addWidget(self.price_table)
        price_footer=QHBoxLayout();self.price_notice=label('',12,'#6b7f85');price_footer.addWidget(self.price_notice,1)
        self.price_help=HelpButton('','票价来源与估算说明');price_footer.addWidget(self.price_help);layout.addLayout(price_footer)
        layout.addWidget(label('每日白昼',17,bold=True))
        daily=QTableWidget(len(payload['daylight_seconds']),3);daily.setHorizontalHeaderLabels(['日期序号','白昼在车','白昼离车（非净游览）'])
        daily.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);daily.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row,(day,seconds) in enumerate(payload['daylight_seconds'].items()):
            for col,value in enumerate([f'第 {int(day)+1} 天',duration(seconds),duration((request['daylight_end']-request['daylight_start'])*60-seconds)]):daily.setItem(row,col,QTableWidgetItem(value))
        daily.setFixedHeight(45+len(payload['daylight_seconds'])*32);layout.addWidget(daily)
        footer=QHBoxLayout();self.warning=label('资格与余票待核实'+(' · 模拟数据' if payload.get('simulated') else ''),12,'#9a6724');footer.addWidget(self.warning,1)
        footer.addWidget(HelpButton('卧铺、物理线路、学生资格和余票仍需核实，参考价格不等于可购票。\n方案编号：'+payload['id'],'方案与核验说明'));layout.addLayout(footer);layout.addStretch()
        self.setWidget(body);self.date.currentIndexChanged.connect(self.show_prices);self.show_prices()

    def show_prices(self):
        from ..fares import present_fares
        summary,rows,notice=present_fares(self.payload,self.date.currentText(),self.request['budget'],self.assessment)
        self.price_summary.setText(summary);self.price_help.set_help(notice)
        has_price=any(values[3]!='—' or values[4] not in ('—','待核价') for values in rows)
        self.price_notice.setText(notice.split('。',1)[0] if has_price else '尚无可用参考价')
        self.price_table.setRowCount(len(rows))
        for row,values in enumerate(rows):
            for col,value in enumerate(values):
                shown=str(value)
                if col==5 and shown.startswith('https://kyfw.12306.cn'):
                    shown='12306 · 学生估算' if ' · ' in shown else '12306 参考价'
                item=QTableWidgetItem(shown);item.setToolTip(str(value));self.price_table.setItem(row,col,item)
        self.price_table.resizeRowsToContents()

    def set_assessment(self,assessment):
        self.assessment=assessment;self.show_prices()
