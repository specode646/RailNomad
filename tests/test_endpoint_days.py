import json
import pytest
from railnomad.domain.model import Request,daylight,nights_for
from railnomad.domain.search import new_run,search
from railnomad.query import query
from test_requirements_audit import build


@pytest.mark.parametrize('enabled,middle,expected',[(True,180,0),(False,180,1),(True,780,1)])
def test_k242_two_nights_cannot_exempt_the_whole_middle_day(store,tmp_path,enabled,middle,expected):
    snap=build(store,tmp_path,[('t','K242','s','')],[('t','a','23:50:00',1),('t','c','55:36:00',2)])
    request=Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31',allow_short_hsr=False,limit_daylight=enabled,max_daylight_on_train=middle)
    assert len(nights_for(23*3600+50*60,55*3600+36*60))==2
    totals,allowed=daylight([dict(dep=23*3600+50*60,arr=55*3600+36*60)],request)
    assert totals[1]==13*3600 and allowed==bool(expected)
    run=new_run(store,snap,request);search(store,run);assert query(store,run)[1]==expected


def test_open_prefix_day_may_be_the_final_day_after_a_short_connection(store,tmp_path):
    snap=build(store,tmp_path,[('n','K1','s',''),('h','D1','s','')],[('n','a','20:00:00',1),('n','b','33:00:00',2),('h','b','09:30:00',1),('h','c','10:00:00',2)])
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,departure_date='2026-12-31',limit_daylight=True,max_daylight_on_train=0))
    search(store,run);rows,total,_=query(store,run)
    assert total==1
    p=json.loads(rows[0]['payload']);assert len(p['legs'])==2 and p['daylight_seconds']['1']==150*60


@pytest.mark.parametrize('first,expected',[(149,0),(150,1)])
def test_departure_day_limit_counts_short_train_and_overnight_train_together(store,tmp_path,first,expected):
    snap=build(store,tmp_path,[('h','D1','s',''),('n','K1','s','')],[('h','a','17:00:00',1),('h','b','17:20:00',2),('n','b','17:50:00',1),('n','c','32:00:00',2)])
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1,departure_date='2026-12-31',depart_start=17*60,limit_daylight=True,max_daylight_on_train=180,first_daylight_limit=first))
    search(store,run);assert query(store,run)[1]==expected


def test_same_calendar_day_must_meet_both_endpoint_limits():
    leg=dict(dep=30*60,arr=8*3600+30*60)
    assert not daylight([leg],Request('a','c',limit_daylight=True,max_daylight_on_train=180,first_daylight_limit=120,last_daylight_limit=60))[1]
    assert daylight([leg],Request('a','c',limit_daylight=True,max_daylight_on_train=180,first_daylight_limit=120,last_daylight_limit=90))[1]
