"""Dated indexes must preserve the full route/date set, not just examples."""
import json
from dataclasses import replace
from datetime import date

import pytest

from railnomad.domain.model import Request
from railnomad.domain.search import available_departure_dates,new_run,search
from railnomad.query import query
from test_continuity import connection_snapshot
from test_requirements_audit import build


def signatures(store,run,day=None):
    found=set()
    for row in query(store,run,page_size=100000)[0]:
        payload=json.loads(row['payload'])
        for start in payload['dates']:
            if day is None or day==start:
                found.add((tuple((l['trip'],l['from'],l['to'],l['dep'],l['arr'],l['short']) for l in payload['legs']),start))
    return found


@pytest.mark.parametrize('leading',[True,False])
@pytest.mark.parametrize('consecutive',[True,False])
def test_dated_short_connections_equal_all_dates(store,tmp_path,leading,consecutive):
    snap=connection_snapshot(store,tmp_path,leading=leading,short_dep='17:59:00',short_arr='18:19:00',sleep_dep='20:12:00',sleep_arr='30:48:00')
    request=Request('a','c',min_nights=1,max_nights=2,consecutive=consecutive)
    all_run=new_run(store,snap,request);search(store,all_run)
    for day in available_departure_dates(store.snapshot(snap),date(2026,1,1)):
        selected=new_run(store,snap,replace(request,departure_date=day));search(store,selected)
        assert signatures(store,selected)==signatures(store,all_run,day)


def test_date_means_boarding_day_after_service_midnight(store,tmp_path):
    snap=build(store,tmp_path,[('t','Y','s','')],[('t','a','24:30:00',1),('t','c','32:00:00',2)],days=[['s','20261231',1]])
    assert available_departure_dates(store.snapshot(snap))==['2027-01-01']
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1,departure_date='2027-01-01'))
    search(store,run)
    rows,total,_=query(store,run)
    assert total==1
    payload=json.loads(rows[0]['payload'])
    assert payload['dates']==['2027-01-01']
    assert payload['legs'][0]['service_day_offset']==-1
    with pytest.raises(ValueError,match='当前快照覆盖'):
        new_run(store,snap,Request('a','c',departure_date='2026-12-31'))


@pytest.mark.parametrize('value',['2026-13-01','20261231',True,5])
def test_invalid_departure_date(value):
    with pytest.raises(ValueError,match='出发日期'):
        Request('a','c',departure_date=value).validate()


def test_past_date_is_not_selectable(store,snapshot):
    with pytest.raises(ValueError,match='今天或以后'):
        new_run(store,snapshot,Request('a','c',departure_date='2020-01-01'))


def test_dated_multi_night_through_chain_matches_all(store,tmp_path):
    snap=build(store,tmp_path,[('t','Y','s','')],[('t','a','20:00:00',1),('t','c','56:00:00',2)])
    request=Request('a','c',min_nights=2,max_nights=2,min_off_train=0,limit_daylight=False,departure_date='2026-12-31')
    selected=new_run(store,snap,request);search(store,selected)
    all_run=new_run(store,snap,replace(request,departure_date=None));search(store,all_run)
    assert signatures(store,selected)==signatures(store,all_run,request.departure_date)
    assert signatures(store,selected)


def test_dated_through_calendar_is_intersection(store,tmp_path):
    trips=[('a1','Y1','s','block'),('b1','Y2','s','block'),('c1','Y3','one','block')]
    calls=[('a1','a','20:00:00',1),('a1','b','26:00:00',2),('b1','b','26:00:00',1),('b1','c','32:00:00',2),('c1','c','32:00:00',1),('c1','d','33:00:00',2)]
    snap=build(store,tmp_path,trips,calls,[['s','20261231',1],['s','20270101',1],['one','20270101',1]])
    for destination,day,expected in [('c','2026-12-31',1),('d','2026-12-31',0),('d','2027-01-01',1)]:
        request=Request('a',destination,min_nights=1,max_nights=1,departure_date=day)
        run=new_run(store,snap,request);search(store,run)
        assert query(store,run)[1]==expected


def test_cancel_date_preparation_then_resume(store,snapshot):
    from railnomad.data.release import Cancelled
    request=Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31')
    run=new_run(store,snapshot,request)
    stop=[False]
    def progress(**event):
        if event.get('stage')=='建立日期接续索引':stop[0]=True
    with pytest.raises(Cancelled):search(store,run,progress,cancel=lambda:stop[0])
    assert store.rows('SELECT status FROM runs WHERE id=?',(run,))[0]['status']=='cancelled'
    search(store,run)
    expected=new_run(store,snapshot,request);search(store,expected)
    assert signatures(store,run)==signatures(store,expected)


def test_static_city_pruning_uses_only_ridden_subpaths(store,tmp_path):
    trips=[(name,name,'s','') for name in ('base','good','badback','badpass','part')]
    calls=[('base','a','20:00:00',1),('base','b','32:00:00',2),
           ('good','b','20:00:00',1),('good','c','32:00:00',2),
           ('badback','b','20:00:00',1),('badback','a','26:00:00',2),('badback','c','32:00:00',3),
           ('badpass','a','20:00:00',1),('badpass','c','30:00:00',2),('badpass','b','32:00:00',3),
           ('part','c','08:00:00',1),('part','a','20:00:00',2),('part','b','32:00:00',3)]
    snap=build(store,tmp_path,trips,calls)
    run=new_run(store,snap,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    search(store,run)
    rows,total,_=query(store,run)
    assert total==2
    assert {json.loads(row['payload'])['legs'][0]['trip'] for row in rows}=={'base','part'}
    assert all(json.loads(row['payload'])['legs'][1]['trip']=='good' for row in rows)
