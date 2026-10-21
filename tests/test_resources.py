import json
from collections import namedtuple
import pytest
from railnomad import resources
from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.query import query
from railnomad.export import export_run


def test_defaults_and_shared_count_limit():
    normal=Request('a','c');short=Request('a','c',allow_short_hsr=True,max_nights=20,consecutive=False)
    assert not normal.allow_short_hsr and normal.short_hsr_max==60
    assert resources.limits(normal)==dict(results=300000,seconds=1200)
    assert resources.limits(short)==dict(results=300000,seconds=600)
    resources.validate_request(short)  # broad searches require warnings, not a hidden ban
    assert len(resources.risk_warnings(short))==4
    assert '300,000' in resources.description(short)
    with pytest.raises(ValueError,match='60'):resources.validate_request(Request('a','c',allow_short_hsr=True,short_hsr_max=61))


@pytest.mark.parametrize('short,nights,warn',[(True,4,False),(True,5,True),(False,6,False),(False,7,True)])
def test_night_warning_thresholds(short,nights,warn):
    request=Request('a','c',allow_short_hsr=short,max_nights=nights,departure_date='2026-12-31')
    assert any('超过' in text for text in resources.risk_warnings(request))==warn


def test_exact_result_limit_partial_checkpoint_and_resume(store,snapshot,monkeypatch):
    monkeypatch.setattr(resources,'limits',lambda request:dict(results=1,seconds=1200))
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    audit=search(store,run)
    assert audit['status']=='limited' and audit['complete_candidates']==1 and query(store,run)[1]==1
    assert '1 条' in audit['resource_stop']
    again=search(store,run);assert again['status']=='limited' and query(store,run)[1]==1
    monkeypatch.setattr(resources,'limits',lambda request:dict(results=100,seconds=1200))
    finished=search(store,run)
    assert finished['status']=='completed' and query(store,run)[1]==2
    with store.connect() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()


@pytest.mark.parametrize('condition',['time','catalog','free'])
def test_resource_stops_save_empty_partial_without_claiming_completion(store,snapshot,monkeypatch,condition):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    if condition=='time':monkeypatch.setattr(resources,'limits',lambda request:dict(results=300000,seconds=0))
    elif condition=='catalog':monkeypatch.setattr(resources,'CATALOG_BYTES',1)
    else:
        usage=namedtuple('Usage','total used free')
        monkeypatch.setattr(resources.shutil,'disk_usage',lambda path:usage(10,9,1))
    audit=search(store,run)
    assert audit['status']=='limited' and audit['resource_stop']
    assert store.rows('SELECT status FROM runs WHERE id=?',(run,))[0]['status']=='limited'


def test_export_disk_exhaustion_removes_partial_output(store,snapshot,tmp_path,monkeypatch):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    search(store,run);target=tmp_path/'exports';target.mkdir()
    checks=[0]
    def check(path):
        checks[0]+=1
        if checks[0]>=2:raise resources.ResourceLimit('磁盘剩余不足 5 GiB')
    monkeypatch.setattr(resources,'check_free',check)
    with pytest.raises(resources.ResourceLimit):export_run(store,run,target,{},'任务全部')
    assert not list(target.iterdir()) and not store.rows('SELECT * FROM exports')


def test_worker_reports_early_preparation_resource_stop_as_partial(store,snapshot,tmp_path,monkeypatch):
    from railnomad import worker
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    def stop(*args,**kwargs):raise resources.ResourceLimit('准备数据达到上限')
    monkeypatch.setattr('railnomad.domain.search.Network',stop)
    job=tmp_path/'job.json';job.write_text(json.dumps(dict(operation='search',run_id=run)),encoding='utf-8')
    assert worker.execute(str(job),store.home)==0
    events=[json.loads(line) for line in (tmp_path/'job.json.events').read_text().splitlines()]
    assert events[-1]['event']=='done' and events[-1]['result']['status']=='limited'
    assert store.rows('SELECT status FROM runs WHERE id=?',(run,))[0]['status']=='limited'


def test_planner_defaults_and_partial_status_are_visible(store,snapshot):
    from PySide6.QtWidgets import QApplication
    from railnomad.ui.main import MainWindow
    application=QApplication.instance() or QApplication([])
    window=MainWindow(store.home);window.suppress_fare_sync=True
    assert not window.planner.fields['allow_short_hsr'].isChecked()
    assert window.planner.fields['min_sleeper_minutes'].isVisibleTo(window.planner)
    assert window.planner.short_settings.isHidden()
    assert not window.planner.fields['short_hsr_max'].isVisibleTo(window.planner)
    assert window.planner.fields['short_hsr_max'].value()==window.planner.fields['short_hsr_max'].maximum()==60
    planner=window.planner;planner.fields['max_nights'].setValue(6);assert planner.night_warning.isHidden()
    planner.fields['max_nights'].setValue(7);assert not planner.night_warning.isHidden() and '6 晚' in planner.night_warning.text()
    planner.fields['max_nights'].setValue(4);planner.fields['allow_short_hsr'].setChecked(True);assert planner.night_warning.isHidden()
    assert not planner.short_settings.isHidden() and planner.fields['short_hsr_max'].isVisibleTo(planner)
    planner.fields['max_nights'].setValue(5);assert not planner.night_warning.isHidden() and '4 晚' in planner.night_warning.text()
    planner.mode.setCurrentIndex(1);assert planner.night_warning.isHidden()
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    with store.connect() as db:db.execute("UPDATE runs SET status='limited',audit=? WHERE id=?",(json.dumps(dict(resource_stop='达到方案数上限')),run))
    window.tasks.refresh();window.results.load(run)
    assert '受限停止' in window.tasks.table.item(0,4).text()
    assert '结果不完整' in window.results.info.text() and '达到方案数上限' in window.results.info.toolTip()
    window.close();application.processEvents()
