"""End-to-end regression for earliest arrival at the final destination."""
import json,zipfile
import pytest
from conftest import fixture_zip
from railnomad.data.gtfs import import_gtfs
from railnomad.domain.model import Request,arrival_allowed
from railnomad.domain.search import new_run,search
from railnomad.query import query


@pytest.mark.parametrize('arrival,expected',[
    ('29:40:00',0),('29:59:59',0),('30:00:00',1),('30:00:01',1),
    ('34:00:01',1),  # A terminal arrival after 10:00 is exempt from the upper bound.
])
def test_terminal_arrival_respects_lower_bound(store,tmp_path,arrival,expected):
    path=fixture_zip(tmp_path/'simulated-arrival.zip')
    with zipfile.ZipFile(path) as z:tables={n:z.read(n) for n in z.namelist()}
    tables['trips.txt']=b'route_id,service_id,trip_id,trip_short_name\nr,s1,t1,T370\n'
    # For the late-arrival case, explicitly allow one extra daytime minute
    # to isolate arrival-window semantics from the independent daylight rule.
    tables['stop_times.txt']=(f'trip_id,arrival_time,departure_time,stop_id,stop_sequence\nt1,21:00:00,21:00:00,a,1\nt1,{arrival},{arrival},c,2\n').encode()
    with zipfile.ZipFile(path,'w') as z:
        for n,v in tables.items():z.writestr(n,v)
    snap=import_gtfs(path,store,{'source':'simulated'})
    request=Request('a','c',min_nights=1,max_nights=1,allow_short_hsr=False,limit_daylight=True,min_off_train=599 if arrival=='34:00:01' else 600)
    run=new_run(store,snap,request);search(store,run)
    assert query(store,run)[1]==expected


def test_custom_lower_bound_and_intermediate_upper_bound():
    request=Request('a','c',arrive_start=390)
    assert not arrival_allowed(6*3600+29*60+59,request,terminal=True)
    assert arrival_allowed(6*3600+30*60,request,terminal=True)
    assert arrival_allowed(11*3600,request,terminal=True)
    assert not arrival_allowed(11*3600,request,terminal=False)
