import json
from dataclasses import replace

import pytest

from railnomad.domain.model import Request,daylight,daylight_rule_text
from railnomad.domain.search import new_run,search
from railnomad.query import query
from test_requirements_audit import build


def test_default_daylight_limits_can_still_be_disabled():
    request=Request('a','c')
    totals,allowed=daylight([dict(dep=21*3600,arr=86400+11*3600+51*60)],request)
    assert request.limit_daylight and allowed and totals[1]==291*60
    assert not replace(request,limit_daylight=False).limit_daylight
    assert daylight([dict(dep=21*3600,arr=86400+11*3600+51*60)],replace(request,limit_daylight=True))[1]
    assert not daylight([dict(dep=21*3600,arr=86400+11*3600+51*60)],replace(request,limit_daylight=True,last_daylight_limit=180))[1]


@pytest.mark.parametrize('enabled,maximum,expected',[(False,180,1),(True,180,0),(True,291,1),(True,290,0)])
def test_late_terminal_is_retained_unless_daylight_limit_is_enabled(store,tmp_path,enabled,maximum,expected):
    snap=build(store,tmp_path,[('t','K1','s','')],[('t','a','21:00:00',1),('t','c','35:51:00',2)])
    request=Request('a','c',min_nights=1,max_nights=1,departure_date='2026-12-31',allow_short_hsr=False,limit_daylight=enabled,max_daylight_on_train=180,last_daylight_limit=maximum)
    run=new_run(store,snap,request);search(store,run)
    rows,count,_=query(store,run);assert count==expected
    if count:assert json.loads(rows[0]['payload'])['daylight_seconds']['1']==291*60


@pytest.mark.parametrize('enabled,maximum,expected',[(False,100,1),(True,100,0),(True,110,1)])
def test_middle_leg_stays_limited_with_unlimited_endpoints(store,tmp_path,enabled,maximum,expected):
    snap=build(store,tmp_path,[('t1','K1','s',''),('t2','K2','s',''),('t3','K3','s','')],[('t1','a','20:00:00',1),('t1','b','32:00:00',2),('t2','b','19:10:00',1),('t2','c','32:50:00',2),('t3','c','20:00:00',1),('t3','d','32:00:00',2)],days=[['s','20261231',1],['s','20270101',1],['s','20270102',1]])
    run=new_run(store,snap,Request('a','d',min_nights=3,max_nights=3,departure_date='2026-12-31',allow_short_hsr=False,limit_daylight=enabled,max_daylight_on_train=maximum))
    search(store,run);assert query(store,run)[1]==expected


def test_turning_off_daylight_keeps_arrival_window_and_short_hsr_window(store,tmp_path):
    snap=build(store,tmp_path,[('t','K1','s','')],[('t','a','21:00:00',1),('t','c','29:40:00',2)])
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1,allow_short_hsr=False))
    search(store,run);assert query(store,run)[1]==0
    with pytest.raises(ValueError,match='白昼在车上限'):Request('a','c',limit_daylight=True,max_daylight_on_train=1000).validate()
    assert '不限制' in daylight_rule_text(Request('a','c',limit_daylight=False).to_dict())
    assert '180' in daylight_rule_text({'daylight_start':420,'daylight_end':1200,'min_off_train':600})


def test_planner_checkbox_and_minutes_are_frozen_independently(store,snapshot,monkeypatch):
    from PySide6.QtWidgets import QApplication,QMessageBox
    from railnomad.ui.main import MainWindow
    app=QApplication.instance() or QApplication([]);window=MainWindow(store.home)
    planner=window.planner
    planner.origin.kind.setCurrentIndex(1);planner.destination.kind.setCurrentIndex(1)
    assert planner.origin.select_place('station','a') and planner.destination.select_place('station','c')
    assert planner.fields['max_daylight_on_train'].value()==180
    assert planner.fields['first_daylight_limit'].isEnabled()
    assert 'min_off_train' not in window.results.extra
    created=[];monkeypatch.setattr(window,'run_search',created.append)
    monkeypatch.setattr(QMessageBox,'exec',lambda _:QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(window,'message',lambda text:pytest.fail(text))
    planner.submit()
    planner.fields['max_daylight_on_train'].setValue(90);planner.submit()
    assert planner.fields['first_daylight_limit'].isEnabled()
    planner.fields['first_daylight_limit'].setValue(240);planner.fields['last_daylight_limit'].setValue(300);planner.submit()
    requests=[json.loads(store.rows('SELECT request FROM runs WHERE id=?',(run,))[0]['request']) for run in created]
    assert requests[0]['limit_daylight'] is True and requests[0]['max_daylight_on_train']==180
    assert requests[1]['limit_daylight'] is True and requests[1]['max_daylight_on_train']==90
    assert requests[1]['first_daylight_limit']==240 and requests[1]['last_daylight_limit']==300
    assert requests[2]['first_daylight_limit']==240 and requests[2]['last_daylight_limit']==300
    planner.fields['max_daylight_on_train'].setValue(None);planner.submit()
    only_endpoints=json.loads(store.rows('SELECT request FROM runs WHERE id=?',(created[-1],))[0]['request'])
    assert only_endpoints['limit_daylight'] and only_endpoints['max_daylight_on_train']==780
    assert only_endpoints['first_daylight_limit']==240
    planner.fields['first_daylight_limit'].choice.setCurrentIndex(1)
    planner.fields['max_daylight_on_train'].setValue(120);planner.submit()
    request=json.loads(store.rows('SELECT request FROM runs WHERE id=?',(created[-1],))[0]['request'])
    assert request['first_daylight_limit']==120
    window.close();app.processEvents()


@pytest.mark.parametrize('first,last,expected',[(None,None,1),(180,None,0),(300,None,1),(None,180,0),(None,300,1),(300,180,0)])
def test_first_and_last_each_have_their_own_limit(store,tmp_path,first,last,expected):
    snap=build(store,tmp_path,[('t','K1','s','')],[('t','a','16:00:00',1),('t','c','35:00:00',2)])
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1,departure_date='2026-12-31',allow_short_hsr=False,limit_daylight=True,max_daylight_on_train=0,first_daylight_limit=first,last_daylight_limit=last))
    search(store,run);assert query(store,run)[1]==expected


def test_middle_segments_are_summed_without_endpoint_contributions():
    legs=[dict(dep=0,arr=0),dict(dep=86400+7*3600,arr=86400+8*3600+20*60),dict(dep=86400+18*3600+40*60,arr=86400+20*3600),dict(dep=2*86400,arr=2*86400)]
    request=Request('a','c',limit_daylight=True,max_daylight_on_train=159)
    assert not daylight(legs,request)[1]
    assert daylight(legs,replace(request,max_daylight_on_train=160))[1]
