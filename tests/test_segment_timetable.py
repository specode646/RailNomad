import json
from datetime import date

from railnomad.domain.model import Request,timetable_warning
from railnomad.domain.search import Network,new_run,search,available_departure_dates
from railnomad.query import query
from test_timetable_repair import import_calls


def test_interior_anomaly_preserves_spanning_and_afterward_segments_but_not_bad_endpoints(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','20:00:00','20:00:00'),('b','26:10:00','26:00:00'),('c','32:00:00','32:00:00'),('d','56:00:00','56:00:00')])
    quality=json.loads(snap['quality']);assert quality['time_affected_trips']==['t1'] and quality['quarantined_trips']==[]
    network=Network(snap)
    assert {s['to'] for s in network.segments('a')}=={'c','d'}
    assert network.segments('b')==[]
    assert [s['to'] for s in network.segments('c')]==['d']
    network.close()
    run=new_run(store,snap['id'],Request('a','c',min_nights=1,max_nights=1,departure_date='2026-12-31',allow_short_hsr=False))
    search(store,run);rows,total,_=query(store,run)
    assert total==1
    payload=json.loads(rows[0]['payload']);leg=payload['legs'][0]
    assert 'intermediate_timetable' in payload['pending'] and payload['status']=='pending'
    assert len(leg['timetable_issues'])==2 and '26:10:00' in timetable_warning(leg)
    assert '26:00:00' in timetable_warning(leg)
    from railnomad.export import export_run
    from pathlib import Path
    export=Path(export_run(store,run,tmp_path,{},'segment-anomaly'))
    assert '26:10:00' in (export/'routes.csv').read_text(encoding='utf-8-sig')
    later=new_run(store,snap['id'],Request('c','d',min_nights=1,max_nights=1,departure_date='2027-01-01',allow_short_hsr=False,limit_daylight=False))
    search(store,later);p=json.loads(query(store,later)[0][0]['payload'])
    assert not p['legs'][0]['timetable_issues']


def test_intermediate_station_still_participates_in_city_reentry_and_forbidden_checks(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','20:00:00','20:00:00'),('b','26:10:00','26:00:00'),('c','32:00:00','32:00:00')])
    forbidden=new_run(store,snap['id'],Request('a','c',min_nights=1,max_nights=1,forbidden_cities=['乙城']))
    search(store,forbidden);assert query(store,forbidden)[1]==0
    # Leaving origin city and re-entering it must still be detected even if
    # the intervening station has an unreliable timetable.
    store.set_setting('city_mapping:'+snap['id'],{'c':'甲城'})
    repeated=new_run(store,snap['id'],Request('a','c',min_nights=1,max_nights=1))
    search(store,repeated);assert query(store,repeated)[1]==0


def test_interstation_conflict_flags_both_contradictory_events_without_disabling_other_event(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','20:00:00','20:00:00'),('b','26:00:00','26:00:00'),('c','25:50:00','27:00:00'),('d','56:00:00','56:00:00')])
    network=Network(snap)
    assert network.segments('b')==[]
    assert 'c' not in {s['to'] for s in network.segments('a')}
    assert [s['to'] for s in network.segments('c')]==['d']
    assert network.segments('a')[-1]['timetable_issues']
    network.close()


def test_ambiguous_boarding_date_is_not_offered_and_train_night_origin_is_unknown(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','20:00:00','20:00:00'),('b','19:00:00','49:00:00'),('c','57:00:00','57:00:00')])
    dates=available_departure_dates(snap,earliest=date(2026,12,31))
    assert '2026-12-31' not in dates and '2027-01-02' in dates
    # An unknown original departure must not fabricate the train's night number.
    run=new_run(store,snap['id'],Request('b','c',min_nights=1,max_nights=1,departure_date='2027-01-02',allow_short_hsr=False))
    search(store,run);rows,total,_=query(store,run)
    assert total==1
    assert json.loads(rows[0]['payload'])['legs'][0]['train_night'] is None


def test_unused_origin_arrival_and_terminus_departure_do_not_disqualify_used_events(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','21:00:00','20:00:00'),('b','26:00:00','26:00:00'),('c','32:00:00','31:59:00')])
    network=Network(snap)
    assert {s['to'] for s in network.segments('a')}=={'b','c'}
    assert not network.segments('a')[-1]['timetable_issues']
    warning=timetable_warning(network.segments('a')[-1])
    assert '未用于本段计算' in warning and '31:59:00' in warning and '21:00:00' in warning
    network.close()
    run=new_run(store,snap['id'],Request('a','c',min_nights=1,max_nights=1))
    search(store,run);assert query(store,run)[1]==1
