import logging
import time
from pathlib import Path
from PySide6.QtCore import QUrl, QTimer,Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QListWidget,
    QStackedWidget,QLabel,QPushButton,QProgressBar,QMessageBox)
from ..storage import Store
from .. import __version__
from .worker import Worker
from .data import DataPage
from .planner import PlannerPage
from .results import ResultsPage
from .tasks import TasksPage
from .widgets import Disclosure


class MainWindow(QMainWindow):
    catalog_changed=Signal(dict)
    def __init__(self,home):
        super().__init__();self.home=home;self.store=Store(home)
        active=self.store.setting('active_snapshot')
        if active:
            from ..data.mapping import inherit_mapping
            inherit_mapping(self.store,active)
            from ..data.reference_mapping import apply_reference
            apply_reference(self.store,active)
        self.callback=None;self.closing=False;self.operation_succeeded=False
        self.job_clock=QTimer(self);self.job_clock.setInterval(1000);self.job_clock.timeout.connect(self.update_elapsed)
        self.worker=Worker(home,self)
        self.worker.event.connect(self.on_event)
        self.worker.finished.connect(self.on_finished)
        self.setWindowTitle(f'RailNomad {__version__} · 夜车旅行规划');self.resize(1280,860)
        root=QWidget();outer=QVBoxLayout(root);row=QHBoxLayout()
        sidebar=QWidget();sidebar.setObjectName('sidebar');sidebar.setFixedWidth(170)
        sidebar_layout=QVBoxLayout(sidebar);sidebar_layout.setContentsMargins(12,22,12,12)
        brand=QLabel('RailNomad');brand.setObjectName('brand');sidebar_layout.addWidget(brand)
        tagline=QLabel('把夜晚交给旅途');tagline.setObjectName('muted');sidebar_layout.addWidget(tagline)
        self.nav=QListWidget();self.nav.setObjectName('navigation');self.nav.addItems(['新建规划','检索任务','我的收藏','数据中心','设置 / 帮助'])
        sidebar_layout.addSpacing(20);sidebar_layout.addWidget(self.nav,1)
        version=QLabel('本地桌面版  '+__version__);version.setObjectName('muted');sidebar_layout.addWidget(version)
        self.pages=QStackedWidget()
        self.planner=PlannerPage(self);self.tasks=TasksPage(self);self.results=ResultsPage(self)
        self.favorites=ResultsPage(self,True);self.data=DataPage(self)
        help_page=QWidget();help_layout=QVBoxLayout(help_page)
        title=QLabel('设置 / 帮助');title.setObjectName('heading');help_layout.addWidget(title)
        help_text=QLabel(f'RailNomad {__version__} · 原生离线桌面应用\n\n先导入或下载运行图，查看质量报告并启用版本，然后新建规划。\n必停城市可选全部满足 AND / 任一满足 OR。\n结果卡片双击打开时间轴、每日行程图及票价明细。\n公开版不提供自动核价；可导入票价证据，并在铁路官方渠道核验。\n学生价按来源规则估算并应用预算；缺价仍保留待核验。\n可选择快照覆盖的今天及以后出发日期，仅检索所选日期。\n城市需有明确归属，站点名称不自动推断城市。\n\n关闭时保存当前检查点并取消后台操作。重启后可继续未完成任务。\n票价更新单独保存，原任务冻结证据保持不变；新任务可复用已查询的同日票价。\n\n本地数据位置：'+str(home))
        help_text.setWordWrap(True);help_layout.addWidget(QLabel('RailNomad '+__version__))
        help_layout.addWidget(Disclosure('使用帮助与数据位置',help_text))
        copyright_text=QLabel('RailNomad 自有代码：MIT License，© 2026 RailNomad contributors。<br><br>'
            '本程序使用 Qt / PySide6 6.8.3，© The Qt Company Ltd 及其他贡献者，按 GNU LGPL v3 使用。'
            '允许修改、替换这些动态库，以及为调试这些修改而进行必要的逆向工程。<br><br>'
            '社区运行图：<a href="https://github.com/wensimehrp/chinese-railway-gtfs">wensimehrp / chinese-railway-gtfs</a>，'
            '<a href="https://creativecommons.org/licenses/by/4.0/">CC BY 4.0</a>（上游声明）。'
            '坐标上游：<a href="https://www.openstreetmap.org/copyright">© OpenStreetMap contributors</a>，ODbL 1.0。'
            '导入会进行格式转换、质量检查及披露的时刻修复。实际任务来源见数据中心及导出报告。')
        copyright_text.setWordWrap(True);copyright_text.setOpenExternalLinks(True)
        help_layout.addWidget(Disclosure('版权、开源许可与数据来源',copyright_text))
        import sys
        license_folder=Path(sys.executable).parent/'third-party-notices' if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]/'third-party'
        licenses_button=QPushButton('打开许可与源码配套目录')
        licenses_button.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(license_folder))))
        help_layout.addWidget(licenses_button)
        open_folder=QPushButton('打开本地数据目录');open_folder.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(home))));help_layout.addWidget(open_folder);help_layout.addStretch()
        for page in (self.planner,self.tasks,self.favorites,self.data,help_page,self.results):self.pages.addWidget(page)
        row.addWidget(sidebar);row.addWidget(self.pages,1);outer.addLayout(row,1)
        progress_row=QHBoxLayout();self.progress_label=QLabel('就绪')
        self.progress=QProgressBar();self.progress.setRange(0,1);self.progress.setValue(0);self.progress.setFixedWidth(180)
        self.progress.setTextVisible(False)
        self.cancel_button=QPushButton('取消');self.cancel_button.setToolTip('取消后台操作，检索任务会保存检查点，可稍后继续。');self.cancel_button.clicked.connect(self.worker.cancel);self.cancel_button.setEnabled(False)
        self.elapsed_label=QLabel('')
        progress_row.addWidget(self.elapsed_label)
        progress_row.addWidget(self.progress_label,1);progress_row.addWidget(self.progress);progress_row.addWidget(self.cancel_button);outer.addLayout(progress_row)
        self.setCentralWidget(root);self.nav.currentRowChanged.connect(self.navigate)
        self.nav.setCurrentRow(0 if self.store.setting('active_snapshot') else 3)
        self.setStyleSheet('''
            QWidget {font-family:'Microsoft YaHei UI';font-size:13px;color:#243b43;}
            QMainWindow {background:#f3f6f8;}
            QWidget#sidebar {background:white;border:1px solid #e5ebee;border-radius:12px;}
            QLabel#brand {font-size:23px;font-weight:700;color:#176e67;}
            QLabel#muted {color:#72858c;font-size:12px;}
            QListWidget#navigation {background:transparent;border:0;padding:0;outline:0;}
            QListWidget#navigation::item {padding:14px 10px;margin:3px 0;border-radius:7px;}
            QListWidget#navigation::item:selected {background:#e1f0ec;color:#12685f;font-weight:600;}
            QLabel#heading {font-size:25px;font-weight:600;padding:6px 0 12px;}
            QPushButton {padding:8px 14px;background:white;border:1px solid #d7e1e5;border-radius:7px;}
            QPushButton:hover {background:#eaf4f1;border-color:#94bdb2;}
            QPushButton:disabled {color:#a2afb4;background:#f5f7f8;}
            QPushButton#primary {background:#16796c;color:white;border-color:#16796c;font-weight:600;}
            QPushButton#primary:hover {background:#10695e;}
            QPushButton#danger {color:#ad4c42;border-color:#e3c5bf;background:#fffafa;}
            QLineEdit,QSpinBox,QDoubleSpinBox,QComboBox,QTimeEdit {padding:6px 8px;background:white;border:1px solid #d7e1e5;border-radius:6px;selection-background-color:#b5ddd2;}
            QLineEdit:focus,QComboBox:focus,QSpinBox:focus,QTimeEdit:focus {border-color:#349c89;}
            QComboBox::drop-down {border:0;width:25px;}
            QComboBox::down-arrow {image:url("__CHEVRON__");width:12px;height:8px;}
            QGroupBox {background:white;border:1px solid #e0e7ea;border-radius:10px;margin-top:14px;padding:18px 16px 14px;}
            QGroupBox::title {subcontrol-origin:margin;left:16px;padding:0 5px;font-weight:600;}
            QScrollArea {border:0;background:transparent;}
            QScrollArea#filterPanel {background:white;border:1px solid #e0e7ea;border-radius:10px;}
            QTableWidget {background:white;border:1px solid #e0e7ea;border-radius:8px;gridline-color:#edf1f3;selection-background-color:#dfefe9;selection-color:#173e37;}
            QHeaderView::section {background:#f5f8f9;padding:9px;border:0;border-bottom:1px solid #e0e7ea;color:#627880;font-weight:600;}
            QToolButton#helpButton {background:#edf3f5;color:#6b838b;border:0;border-radius:10px;min-width:20px;max-width:20px;min-height:20px;max-height:20px;font-weight:600;}
            QToolButton#helpButton:hover {background:#d9eae4;color:#12685f;}
            QToolButton#disclosure {border:0;background:transparent;padding:8px 0;color:#52766d;font-weight:600;}
            QToolButton#actionMenu {padding:8px 14px;background:white;border:1px solid #d7e1e5;border-radius:7px;}
            QMenu {background:white;border:1px solid #d7e1e5;padding:5px;}
            QMenu::item {padding:9px 18px;}
            QMenu::item:selected {background:#e1f0ec;}
            QToolTip {background:#253e44;color:white;border:0;padding:10px;}
            QProgressBar {border:0;border-radius:4px;background:#e4ecee;max-height:8px;}
            QProgressBar::chunk {background:#27917b;border-radius:4px;}
        '''.replace('__CHEVRON__',(Path(__file__).parent/'assets'/'chevron.svg').as_posix()))
        self.catalog_revision=self.store.setting('catalog_revision',0)
        self.catalog_poller=QTimer(self);self.catalog_poller.setInterval(2000)
        self.catalog_poller.timeout.connect(self.check_catalog_revision);self.catalog_poller.start()

    def check_catalog_revision(self):
        revision=self.store.setting('catalog_revision',0)
        if revision!=self.catalog_revision:self.sync_catalog(dict(run_ids=None))

    def sync_catalog(self,change):
        self.catalog_revision=self.store.setting('catalog_revision',0)
        self.store.fragment_cache.clear();self.tasks.refresh()
        self.results.refresh();self.favorites.refresh()
        self.catalog_changed.emit(change)

    def watch_dialog(self,dialog,run_id,result_ids=None,refresh=None):
        def changed(change):
            if change.get('run_ids') is not None and run_id not in change['run_ids']:return
            exists=bool(self.store.rows('SELECT 1 FROM runs WHERE id=?',(run_id,)))
            missing=result_ids and any(not self.store.rows('SELECT 1 FROM results WHERE id=?',(identity,)) for identity in result_ids)
            if not exists or missing or not result_ids and refresh is None:dialog.reject()
            elif refresh:refresh()
        self.catalog_changed.connect(changed)
        def detach(*_):
            try:self.catalog_changed.disconnect(changed)
            except (RuntimeError,TypeError):pass
        dialog.finished.connect(detach)

    def delete_catalog(self,scope,run_ids,result_ids=(),patterns=()):
        if self.worker.running:
            self.message('请先取消或等待当前后台操作完成，再删除。');return
        from ..deletion import preview
        selection=preview(self.store,scope,run_ids,result_ids,patterns)
        if not selection['deleted_results'] and not selection['deleted_runs']:
            self.sync_catalog(selection);return
        if scope=='tasks':description=f"删除 {selection['deleted_runs']} 个检索任务及其 {selection['deleted_results']:,} 条方案？"
        elif scope=='patterns':description=f"删除 {selection['deleted_groups']} 个城市路线组及其全部 {selection['deleted_results']:,} 条方案？\n包含被当前筛选隐藏的方案。"
        else:description=f"删除选中的 {selection['deleted_results']:,} 条具体方案？"
        choice=QMessageBox.question(self,'确认删除',description+'\n相关收藏与费用记录同步移除。\n此前导出到文件夹的文件不会被删除。',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.Cancel,QMessageBox.StandardButton.Cancel)
        if choice!=QMessageBox.StandardButton.Yes:return
        def done(result):
            self.sync_catalog(result)
            self.progress_label.setText(f"删除完成 · {result['deleted_runs']} 个任务 · {result['deleted_results']:,} 条方案")
        self.start_job('delete',done,scope=scope,run_ids=list(run_ids),result_ids=list(result_ids),patterns=list(patterns))

    def navigate(self,index):
        self.pages.setCurrentIndex(index)
        if index==1:self.tasks.refresh()
        if index==2:self.favorites.refresh()
        if index==3:self.data.refresh()

    def message(self,text):
        QMessageBox.information(self,'RailNomad',text)

    def start_job(self,operation,callback=None,**fields):
        if self.worker.running:
            self.message('请等待当前后台任务完成，或先取消。');return False
        self.operation_succeeded=False;self.started_at=time.monotonic();self.job_clock.start();self.update_elapsed()
        self.callback=callback;self.progress_label.setText('正在准备…');self.progress.setRange(0,0)
        self.cancel_button.setEnabled(True)
        self.worker.start(operation,**fields)
        return True

    def run_search(self,identity):
        if self.start_job('search',lambda result:self.search_done(identity),run_id=identity):
            self.tasks.refresh();self.nav.setCurrentRow(1)

    def search_done(self,identity):
        self.tasks.refresh();self.results.load(identity);self.pages.setCurrentWidget(self.results)

    def on_event(self,event):
        kind=event.get('event')
        if kind=='done':
            self.operation_succeeded=True
            result=event['result']
            message='操作完成'
            if isinstance(result,dict) and result.get('status')=='limited':
                message='受限停止 · 结果不完整 · '+result.get('resource_stop','达到资源上限')
            if isinstance(result,dict) and 'checked' in result:
                message='已存票价没有变化，无需重复同步' if result.get('unchanged') else f"票价同步完成 · 已检查 {result['checked']:,} 条方案 · {result['updated']:,} 条匹配已存报价"
            self.progress_label.setText(message)
            if self.callback:
                try:self.callback(event['result'])
                except Exception as exc:logging.exception('UI result handling');self.message(str(exc))
        elif kind in ('error','cancelled'):
            self.progress_label.setText(event.get('message','操作结束'))
            self.tasks.refresh()
            if not self.closing:self.message(event.get('message','操作失败')+'\n日志：'+str(self.home/'logs'))
        else:
            self.progress_label.setText(event.get('stage','处理中') +
                (f" · 已检查 {event['current']:,}/{event['total']:,} 条方案 · {event['matched']:,} 条匹配已存报价" if 'matched' in event else
                 f" · 已访问 {event['visited']:,} 状态 · {event['routes']:,} 条候选" if 'visited' in event else f" · {event.get('current',0):,}"))
            if event.get('total'):
                self.progress.setRange(0,1000);self.progress.setValue(int(event['current']/event['total']*1000))
            else:self.progress.setRange(0,0)

    def update_elapsed(self):
        elapsed=int(time.monotonic()-self.started_at)
        self.elapsed_label.setText(f'已用时 {elapsed//60} 分 {elapsed%60} 秒')

    def on_finished(self):
        self.job_clock.stop()
        self.progress.setRange(0,1);self.progress.setValue(1 if self.operation_succeeded else 0);self.cancel_button.setEnabled(False)
        if self.closing:QTimer.singleShot(0,self.close)
        else:self.check_catalog_revision()

    def closeEvent(self,event):
        if self.worker.running:
            if not self.closing:
                choice=QMessageBox.question(self,'保存并退出','后台操作正在进行。是否取消并保存搜索检查点后退出？',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
                if choice!=QMessageBox.StandardButton.Yes:event.ignore();return
                self.closing=True;self.worker.cancel();self.progress_label.setText('等待后台保存并退出…')
            event.ignore()
        else:
            self.catalog_poller.stop();event.accept()
