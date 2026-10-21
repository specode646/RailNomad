import json
import pytest
from railnomad.domain.model import Request,comfort_for_leg,comfort_minutes
from railnomad.domain.search import new_run,search
from railnomad.query import query
from test_requirements_audit import build


@pytest.mark.parametrize('dep,arr,departure,arrival',[
    (18*3600+35*60,86400+6*3600+27*60,55,3),
    (45*60,8*3600,15,0),
    (16*3600,86400+11*3600+51*60,210,141),
    (20*3600,86400+8*3600,0,0),
])
def test_endpoint_comfort_uses_the_actual_night_without_wrapping_early_departures(dep,arr,departure,arrival):
    value=comfort_for_leg(dict(dep=dep,arr=arr,short=False),Request('a','c'))
    assert value==dict(departure=departure,arrival=arrival)


def test_daytime_short_train_does_not_use_sleeper_morning_arrival_window():
    assert comfort_for_leg(dict(dep=17*3600,arr=17*3600+20*60,short=True),Request('a','c'))==dict(departure=0,arrival=0)


def test_extreme_times_remain_eligible_but_comfort_is_ranked_and_filtered(store,tmp_path):
    trips=[(name,name,'s','') for name in ('early','comfort','late','too_early')]
    calls=[('early','a','19:00:00',1),('early','c','30:00:00',2),
           ('comfort','a','20:00:00',1),('comfort','c','32:00:00',2),
           ('late','a','20:00:00',1),('late','c','35:00:00',2),
           ('too_early','a','20:00:00',1),('too_early','c','29:40:00',2)]
    snap=build(store,tmp_path,trips,calls)
    request=Request('a','c',min_nights=1,max_nights=1,departure_date='2026-12-31',allow_short_hsr=False)
    run=new_run(store,snap,request);search(store,run)
    rows,total,_=query(store,run,sort='comfort')
    assert total==3
    payloads=[json.loads(r['payload']) for r in rows]
    assert [p['legs'][0]['trip'] for p in payloads]==['comfort','early','late']
    assert [p['comfort_minutes'] for p in payloads]==[0,60,90]
    assert all(p['comfort_minutes']==comfort_minutes(p,request) for p in payloads)
    assert query(store,run,{'comfort_only':True})[1]==1
    grouped=query(store,run,sort='comfort',grouped=True)[0]
    assert grouped[0]['representative']==rows[0]['id']
    from railnomad.export import export_run
    from pathlib import Path
    target=Path(export_run(store,run,tmp_path,{},'comfort-check'))
    assert '舒适：' in (target/'report.md').read_text(encoding='utf-8')
    assert '偏离 60 分钟' in (target/'route-index.md').read_text(encoding='utf-8')
