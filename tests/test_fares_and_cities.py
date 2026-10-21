import json
from dataclasses import replace

import pytest

from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.query import query
from railnomad.fares import estimate,parse_prices,save_assessment,price_route,PublicFareClient
from railnomad.export import export_run


def candidate(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    search(store,run)
    p=json.loads(query(store,run)[0][0]['payload'])
    return run,p


def assessment(payload,values):
    return {'dates':{payload['dates'][0]:{'queried_at':'2026-10-18T12:00:00+08:00','kind':'live','legs':[
        {'fares':[{'seat':'硬卧','berth':'上铺','adult':value+50,'source':'simulated fixture','student':{'low':value,'high':value,'formula':'fixture'}}]}
        if value is not None else {'fares':[],'error':'simulated missing'} for value in values]}}}


def test_required_cities_and_or_are_search_constraints(store,snapshot):
    base=Request('a','b',min_nights=1,max_nights=1,required_cities=['乙城','丙城'])
    and_run=new_run(store,snapshot,base);search(store,and_run)
    or_run=new_run(store,snapshot,replace(base,required_city_mode='OR'));search(store,or_run)
    assert query(store,and_run)[1]==0
    rows,count,_=query(store,or_run);assert count==1
    assert 'required_city_unverified' not in json.loads(rows[0]['payload'])['pending']
    assert json.loads(store.rows('SELECT request FROM runs WHERE id=?',(or_run,))[0]['request'])['required_city_mode']=='OR'


def test_required_mode_validation():
    with pytest.raises(ValueError,match='AND 或 OR'):Request('a','b',required_city_mode='X').validate()
    with pytest.raises(ValueError,match='同时'):Request('a','b',required_cities=['甲'],forbidden_cities=['甲']).validate()
    Request('a','b',required_cities=['甲','乙'],forbidden_cities=['甲'],required_city_mode='OR').validate()
    with pytest.raises(ValueError,match='同时'):Request('a','b',required_cities=['甲'],forbidden_cities=['甲'],required_city_mode='OR').validate()


def test_student_estimate_uses_actual_rules_and_floor():
    assert estimate(200,seat='硬卧',emu=False,hard_seat=100)['low']==150
    assert estimate(200,seat='硬卧',emu=False) is None
    assert estimate(200,seat='软卧',emu=False,hard_seat=100) is None
    assert estimate(100,seat='硬座',emu=False)['high']==50
    assert estimate(100,seat='二等座',emu=True,published=200)['low']==80
    assert estimate(100,seat='二等座',emu=True,published=300)['high']==100
    r=estimate(100,seat='动卧',emu=True)
    assert (r['low'],r['high'])==(75,100) and r['estimated'] and r['policy']




def test_live_estimates_filter_globally_sort_and_export_without_rewriting(store,snapshot,tmp_path):
    run,p=candidate(store,snapshot)
    before=store.rows('SELECT payload FROM results WHERE id=?',(p['id'],))[0]['payload']
    save_assessment(store,p,assessment(p,[80,80]))
    assert query(store,run,{'budget':159})[1]==1 # only the other unpriced variant remains
    rows,count,_=query(store,run,{'budget':160,'known_price':True},sort='price');assert count==1
    assert rows[0]['price']==160
    grouped,groups,total=query(store,run,{'known_price':True},grouped=True,sort='price')
    assert total==groups==1 and grouped[0]['price']==160 and grouped[0]['unpriced_count']==0
    assert store.rows('SELECT payload FROM results WHERE id=?',(p['id'],))[0]['payload']==before
    path=export_run(store,run,tmp_path,{'ids':[p['id']]},'selected')
    exported=json.loads((__import__('pathlib').Path(path)/'candidates.jsonl').read_text(encoding='utf-8'))
    assert exported['latest_fare_assessment']['dates']==assessment(p,[80,80])['dates']
    import csv
    with (__import__('pathlib').Path(path)/'fares.csv').open(encoding='utf-8-sig',newline='') as stream:
        fares=list(csv.DictReader(stream))
    assert len(fares)==2 and all(float(f['student_low'])==80 for f in fares)


def test_partial_price_exceeding_budget_cannot_be_shown_as_free(store,snapshot):
    run,p=candidate(store,snapshot);save_assessment(store,p,assessment(p,[120,None]))
    rows,count,_=query(store,run,{'budget':100});assert count==1 and rows[0]['id']!=p['id']
    assert query(store,run,{'known_price':True})[1]==0


def test_unqueried_dates_remain_possible_and_unknown(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    p=json.loads(query(store,run)[0][0]['payload']);assert len(p['dates'])==2
    save_assessment(store,p,assessment(p,[800,800]))
    assert query(store,run,{'budget':1,'ids':[p['id']]})[1]==1
    assert query(store,run,{'known_price':True,'ids':[p['id']]})[1]==0








def test_observations_can_be_frozen_by_future_tasks(store,snapshot,tmp_path):
    run,p=candidate(store,snapshot);a=assessment(p,[80,80])
    for detail in a['dates'][p['dates'][0]]['legs']:
        detail['fares'][0]['observed_at']='2026-10-18T12:00:00+08:00'
    save_assessment(store,p,a)
    future=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    checkpoint=json.loads(store.rows('SELECT checkpoint FROM runs WHERE id=?',(future,))[0]['checkpoint'])
    assert len(checkpoint['evidence'])==4
    assert len([r for r in checkpoint['evidence'] if r['passenger']=='student'])==2
    assert not json.loads(store.rows('SELECT checkpoint FROM runs WHERE id=?',(run,))[0]['checkpoint'])['evidence']
    search(store,future)
    path=export_run(store,future,tmp_path,{},'future frozen')
    import csv
    from pathlib import Path
    with (Path(path)/'fares.csv').open(encoding='utf-8-sig',newline='') as stream:
        assert any(row['student_low']=='80' for row in csv.DictReader(stream))


