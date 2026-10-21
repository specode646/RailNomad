"""Regression: no uncovered overnight anywhere in a continuous itinerary."""
import json
import zipfile
import pytest
from conftest import fixture_zip
from railnomad.data.gtfs import import_gtfs
from railnomad.domain.model import Request, RULES_VERSION, route_empty_nights
from railnomad.domain.search import new_run, search
from railnomad.query import query
from railnomad.ui.results import timeline


def connection_snapshot(store, tmp_path, *, leading, short_dep, short_arr, sleep_dep, sleep_arr):
    path=fixture_zip(tmp_path/'simulated-overnight.zip')
    with zipfile.ZipFile(path) as source:
        tables={name:source.read(name) for name in source.namelist()}
    tables['trips.txt']=b'route_id,service_id,trip_id,trip_short_name\nr,s1,short,D3286\nr,s1,sleep,K244\n'
    first,last=('a','b') if leading else ('b','c')
    sf,st=('b','c') if leading else ('a','b')
    tables['stop_times.txt']=(
        'trip_id,arrival_time,departure_time,stop_id,stop_sequence\n'
        f'short,{short_dep},{short_dep},{first},1\n'
        f'short,{short_arr},{short_arr},{last},2\n'
        f'sleep,{sleep_dep},{sleep_dep},{sf},1\n'
        f'sleep,{sleep_arr},{sleep_arr},{st},2\n').encode()
    tables['calendar_dates.txt']=b'service_id,date,exception_type\ns1,20261231,1\ns1,20270101,1\ns1,20270102,1\n'
    with zipfile.ZipFile(path,'w') as target:
        for name,content in tables.items():target.writestr(name,content)
    return import_gtfs(path,store,{'source':'simulated'})


@pytest.mark.parametrize('leading,short_dep,short_arr,sleep_dep,sleep_arr',[
    (True,'17:59:00','18:19:00','20:12:00','30:48:00'),
    (False,'17:59:00','18:19:00','20:00:00','32:00:00'),
])
def test_leading_and_trailing_empty_night_rejected(store,tmp_path,leading,short_dep,short_arr,sleep_dep,sleep_arr):
    snap=connection_snapshot(store,tmp_path,leading=leading,short_dep=short_dep,short_arr=short_arr,sleep_dep=sleep_dep,sleep_arr=sleep_arr)
    strict=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,short_same_station_gap=600,short_same_city_gap=660))
    search(store,strict)
    assert query(store,strict)[1]==0
    flexible=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,consecutive=False,short_same_station_gap=600,short_same_city_gap=660))
    search(store,flexible)
    rows,total,_=query(store,flexible)
    assert total>0
    assert all(route_empty_nights(json.loads(row['payload'])) for row in rows)


@pytest.mark.parametrize('leading,short_dep,short_arr,sleep_dep,sleep_arr',[
    (True,'06:30:00','06:50:00','20:12:00','30:48:00'),
    (False,'17:59:00','18:19:00','20:00:00','30:48:00'),
])
def test_same_day_short_connection_remains_available(store,tmp_path,leading,short_dep,short_arr,sleep_dep,sleep_arr):
    snap=connection_snapshot(store,tmp_path,leading=leading,short_dep=short_dep,short_arr=short_arr,sleep_dep=sleep_dep,sleep_arr=sleep_arr)
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1))
    search(store,run)
    rows,total,_=query(store,run)
    assert total>0
    assert all(not route_empty_nights(json.loads(row['payload'])) for row in rows)


def test_prefix_does_not_prune_current_night_before_later_sleeper(store,tmp_path):
    snap=connection_snapshot(store,tmp_path,leading=True,short_dep='00:10:00',short_arr='00:30:00',sleep_dep='00:40:00',sleep_arr='08:10:00')
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,short_same_station_gap=0))
    search(store,run)
    assert query(store,run)[1]>0


def test_old_continuous_checkpoints_cannot_resume_under_new_rules(store,snapshot):
    request=Request('a','c',min_nights=2,max_nights=2,rules_version='2026-10-18.1-target-hsr')
    old=new_run(store,snapshot,request)
    with pytest.raises(ValueError,match='旧版检索规则'):
        search(store,old)
    assert Request('a','c').rules_version==RULES_VERSION

def test_legacy_empty_night_is_explicit_in_timeline_and_export(store,tmp_path):
    from railnomad.export import export_run
    from railnomad.storage import encode
    from pathlib import Path
    snap=connection_snapshot(store,tmp_path,leading=True,short_dep='17:59:00',short_arr='18:19:00',sleep_dep='20:12:00',sleep_arr='30:48:00')
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,consecutive=False))
    search(store,run)
    payload=next(json.loads(r['payload']) for r in query(store,run)[0] if route_empty_nights(json.loads(r['payload'])))
    legacy=Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,rules_version='2026-10-18.1-target-hsr').to_dict()
    text=timeline(payload,legacy)
    assert text.startswith('不符合连续卧铺规则：第 1 晚')
    assert '没有卧铺覆盖' in text
    with store.connect() as db:db.execute('UPDATE runs SET request=? WHERE id=?',(encode(legacy),run))
    original=store.rows('SELECT payload FROM results WHERE run_id=?',(run,))
    out=export_run(store,run,tmp_path,{},'任务全部')
    assert '旧版检索规则' in (Path(out)/'report.md').read_text(encoding='utf-8')
    assert store.rows('SELECT payload FROM results WHERE run_id=?',(run,))==original

def test_reported_connection_is_kept_on_day_one(store,tmp_path):
    snap=connection_snapshot(store,tmp_path,leading=True,short_dep='17:59:00',short_arr='18:19:00',sleep_dep='20:12:00',sleep_arr='30:48:00')
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1))
    search(store,run)
    rows,total,_=query(store,run)
    assert total==1
    payload=json.loads(rows[0]['payload'])
    assert payload['legs'][1]['dep']==20*3600+12*60
    assert payload['legs'][1]['arr']==86400+6*3600+48*60
    assert payload['legs'][1]['dep']-payload['legs'][0]['arr']==113*60
    assert not route_empty_nights(payload)
    assert '第 1 天 20:12' in timeline(payload,Request('a','c').to_dict())


@pytest.mark.parametrize('departure,expected',[('19:48:00',0),('19:49:00',1)])
def test_short_same_city_transfer_90_minute_boundary(store,tmp_path,departure,expected):
    snap=connection_snapshot(store,tmp_path,leading=True,short_dep='17:59:00',short_arr='18:19:00',sleep_dep=departure,sleep_arr='30:48:00')
    # Explicitly switch the overnight boarding station to the same city's b2.
    # Rebuild the simulated source; never mutate an imported snapshot.
    source=tmp_path/'simulated-overnight.zip'
    with zipfile.ZipFile(source) as z:tables={n:z.read(n) for n in z.namelist()}
    tables['stop_times.txt']=tables['stop_times.txt'].replace(f'sleep,{departure},{departure},b,1'.encode(),f'sleep,{departure},{departure},b2,1'.encode())
    changed=tmp_path/'simulated-station-change.zip'
    with zipfile.ZipFile(changed,'w') as z:
        for n,value in tables.items():z.writestr(n,value)
    snap=import_gtfs(changed,store,{'source':'simulated'})
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1))
    search(store,run)
    assert query(store,run)[1]==expected


@pytest.mark.parametrize('departure,expected',[('18:48:00',0),('18:49:00',1)])
def test_short_same_station_30_minute_boundary(store,tmp_path,departure,expected):
    # Internal sleeper departures still obey their user-configured allowed window.
    snap=connection_snapshot(store,tmp_path,leading=True,short_dep='17:59:00',short_arr='18:19:00',sleep_dep=departure,sleep_arr='30:48:00')
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,depart_start=18*60))
    search(store,run)
    assert query(store,run)[1]==expected


def test_night_train_stopover_rule_is_not_short_transfer_rule():
    from railnomad.domain.model import connection_gap
    request=Request('a','c')
    assert connection_gap(request,True)==600
    assert connection_gap(request,False)==660
    assert connection_gap(request,True,previous_short=True)==30
    assert connection_gap(request,False,next_short=True)==90

