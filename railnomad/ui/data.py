import json
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                              QTableWidget, QTableWidgetItem, QTextEdit, QFileDialog,
                              QMessageBox, QInputDialog, QLabel)
from ..storage import encode
from .widgets import Disclosure,HelpButton


class DataPage(QWidget):
    def __init__(self, main):
        super().__init__()
        self.main = main
        self.store = main.store
        self.release = None
        layout = QVBoxLayout(self)
        title = QLabel('数据中心')
        title.setObjectName('heading')
        layout.addWidget(title)
        intro=QHBoxLayout();intro.addWidget(QLabel('管理本地运行图与数据版本'),1)
        intro.addWidget(HelpButton('从社区运行图建立离线快照。导入后选择版本并点击“使用此版本”，历史任务仍使用原版本。','数据版本说明'));layout.addLayout(intro)
        buttons = QHBoxLayout()
        for text, fn in [('检查最新正式版', self.check), ('下载并导入', self.download),
                         ('导入本地 GTFS ZIP', self.local), ('使用此版本', self.activate),
                         ('导入城市映射', self.mapping), ('导入日期化证据', self.evidence)]:
            button = QPushButton(text)
            button.clicked.connect(fn)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(['版本', '导入时间', '状态', '指纹'])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnHidden(3,True);self.table.verticalHeader().hide()
        self.table.itemSelectionChanged.connect(self.quality)
        layout.addWidget(self.table, 1)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.quality_summary=QLabel('选择一个版本查看数据概况');self.quality_summary.setWordWrap(True);layout.addWidget(self.quality_summary)
        self.details=Disclosure('质量报告与来源详情',self.detail);layout.addWidget(self.details)
        osm_button=QPushButton('导入 OSM 城市归属包')
        osm_button.setToolTip('导入已审查的名称归属包。按站号、站名和坐标核对身份，替换所选版本供新任务使用的归属；历史任务不变。')
        osm_button.clicked.connect(self.osm_mapping)
        layout.addWidget(osm_button)
        self.refresh()

    def refresh(self):
        self.snapshots = self.store.snapshots()
        active = self.store.setting('active_snapshot')
        self.table.setRowCount(len(self.snapshots))
        for i, snap in enumerate(self.snapshots):
            for col, value in enumerate([snap['tag'], snap['created'][:19], '使用中' if snap['id'] == active else '可用', snap['id'][:16]]):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.table.resizeColumnsToContents()

    def selected(self):
        row = self.table.currentRow()
        if row < 0:
            raise ValueError('请先选择一个已安装版本')
        return self.snapshots[row]

    def quality(self):
        if self.table.currentRow() < 0:
            return
        snap = self.selected()
        quality=json.loads(snap['quality']);calendar=' — '.join(quality.get('calendar',[]))
        self.quality_summary.setText(f"{calendar}   ·   修复 {len(quality.get('repaired_trips',[]))} 趟   ·   局部异常 {len(quality.get('time_affected_trips',[]))} 趟   ·   隔离 {len(quality.get('quarantined_trips',[]))} 趟")
        mapping=self.store.setting('city_mapping:'+snap['id'],{})
        self.quality_summary.setText(self.quality_summary.text()+f'   ·   外部城市映射 {len(mapping):,} 站')
        provenance=self.store.setting('city_mapping_provenance:'+snap['id'],{})
        if provenance:
            self.quality_summary.setText(self.quality_summary.text()+'   ·   '+provenance['provider'])
        self.detail.setPlainText('数据质量及来源（未知不代表已核实）\n' + json.dumps(
            dict(quality=json.loads(snap['quality']), metadata=json.loads(snap['metadata']),city_mapping=provenance), ensure_ascii=False, indent=2))
        from ..attribution import data_notices
        self.detail.setPlainText(data_notices(snap)+'\n\n'+self.detail.toPlainText())

    def check(self):
        self.main.start_job('latest', self.checked)

    def checked(self, result):
        self.release = result
        r = result['release']
        assets = result['assets']
        digests={a.get('digest','').removeprefix('sha256:') for a in assets if a.get('digest')}
        installed={json.loads(s['metadata']).get('sha256',s['id']) for s in self.store.snapshots()}
        active=self.store.setting('active_snapshot')
        active_digest=json.loads(self.store.snapshot(active)['metadata']).get('sha256',active) if active else None
        state='当前已启用该内容' if active_digest in digests else '已安装，可在版本列表启用' if installed & digests else '尚未安装此附件内容（无摘要时请导入后比对）'
        self.quality_summary.setText(f"最新版本 {r['tag_name']} · {state}")
        self.detail.setPlainText(state+'\n'+f"最新正式版：{r['tag_name']}\n发布时间：{r['published_at']}\n来源：{r['html_url']}\n" +
                                 '\n'.join(f"{a['name']} · {a['size']:,} 字节 · {a.get('digest') or '无上游摘要'}" for a in assets))

    def download(self):
        if self.release is None:
            self.main.message('请先检查最新正式版')
            return
        assets = self.release['assets']
        if len(assets) > 1:
            choice, ok = QInputDialog.getItem(self, '选择数据附件', '请选择 GTFS ZIP，源代码 ZIP 不能作为运行图', [a['name'] for a in assets], 0, False)
            if not ok:
                return
            asset = next(a for a in assets if a['name'] == choice)
        else:
            asset = assets[0]
        r = self.release['release']
        metadata = {'repo': 'wensimehrp/chinese-railway-gtfs', 'release_id': r['id'],
                    'tag': r['tag_name'], 'asset_id': asset['id'], 'asset_name': asset['name'],
                    'published_at': r['published_at'], 'url': asset['browser_download_url'],
                    'digest': asset.get('digest'), 'license': 'CC BY 4.0 (upstream declaration)', 'source': 'community_gtfs'}
        self.main.start_job('download', self.imported, asset=asset, metadata=metadata)

    def local(self):
        path, _ = QFileDialog.getOpenFileName(self, '导入 GTFS', '', 'GTFS ZIP (*.zip)')
        if path:
            self.main.start_job('import', self.imported, path=path, metadata={'source': 'local_gtfs', 'license': '未提供，需查看原始来源'})

    def imported(self, identity):
        self.refresh()
        current=self.store.snapshot(identity)
        changed=any(s['tag']==current['tag'] and s['id']!=identity for s in self.snapshots)
        note=('同一原始附件按新解析规则生成快照，原版本保留；同来源城市映射及日期证据已复用。\n'
              if json.loads(current['metadata']).get('same_source_predecessors') else
              '同名版本附件内容发生变化，已保留原版本并新增快照。\n' if changed else '')
        count=len(self.store.setting('city_mapping:'+identity,{}))
        self.main.message(note+f'导入完成，城市映射 {count:,} 站。仅继承站号、站名与坐标完全一致且有来源的归属；其余保持待核对。\n请查看质量报告后选择“使用此版本”。')

    def activate(self):
        try:
            self.store.activate(self.selected()['id'])
            self.refresh()
            self.main.planner.refresh_places()
            self.main.message('已切换供新任务使用的数据版本；历史任务仍引用原快照。')
        except Exception as exc:
            self.main.message(str(exc))

    def mapping(self):
        try:
            snap=self.selected()
            path,_=QFileDialog.getOpenFileName(self,'导入城市归属（CSV：stop_id,city,source）','','CSV (*.csv)')
            if path:
                self.main.start_job('mapping',self.mapping_done,snapshot=snap['id'],path=path)
        except Exception as exc:self.main.message(str(exc))

    def mapping_done(self,count):
        self.main.planner.refresh_places()
        self.quality()
        self.main.message(f'已保存 {count} 条城市映射，新任务会冻结来源及内容。')

    def osm_mapping(self):
        try:
            from ..data.osm_mapping import preview_package
            snap=self.selected()
            path,_=QFileDialog.getOpenFileName(self,'选择已审查的 OSM 名称归属包清单','','JSON (*.json)')
            if not path:return
            preview=preview_package(self.store,snap['id'],path)
            text=(f"覆盖范围：{preview['manifest']['coverage']}\n"
                  f"身份匹配 {len(preview['mapping']):,} 站，未知 {preview['unknown']:,} 站。\n"
                  f"归属变更 {preview['changed']:,} 站，移除旧归属 {preview['removed']:,} 站。\n"
                  '替换所选版本供新任务使用的归属？历史任务保留冻结内容。')
            if QMessageBox.question(self,'启用 OSM 城市归属',text)!=QMessageBox.StandardButton.Yes:return
            self.main.start_job('osm_mapping',self.osm_done,snapshot=snap['id'],path=path)
        except Exception as exc:self.main.message(str(exc))

    def osm_done(self,result):
        self.main.planner.refresh_places();self.quality()
        self.main.message(f"OSM 归属已启用：{result['mapped']:,} 站；{result['unknown']:,} 站保持未知。")

    def evidence(self):
        try:
            snap=self.selected()
            path,_=QFileDialog.getOpenFileName(self,'导入日期化票价 / 卧铺 / 物理区段证据','','JSON (*.json)')
            if path:
                self.main.start_job('evidence',lambda count:self.main.message(f'已保存 {count} 条证据，新任务会使用；历史任务不改写。'),snapshot=snap['id'],path=path)
        except Exception as exc:self.main.message(str(exc))
