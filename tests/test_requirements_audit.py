"""Requirement-driven regressions for the 2026-10-18 semantic audit."""
import csv,io,json,zipfile
from pathlib import Path
import pytest
from conftest import fixture_zip
from railnomad.data.gtfs import import_gtfs
from railnomad.domain.model import Request,window_contains,departure_allowed
from railnomad.domain.search import new_run,search
from railnomad.query import query
from railnomad.evidence import evaluate,import_evidence
from railnomad.export import export_run


def build(store,tmp_path,trips,calls,days=None,route_type=2):
    path=fixture_zip(tmp_path/'audit-simulated.zip')
    with zipfile.ZipFile(path) as z:tables={n:z.read(n) for n in z.namelist()}
    tables['stops.txt']+=b'd,SIMULATED-D,33,113,D-city\n'
    values={
        'routes.txt':[['route_id','route_type'],['r',route_type]],
        'trips.txt':[['route_id','service_id','trip_id','trip_short_name','block_id']]+[['r',service,identity,name,block] for identity,name,service,block in trips],
        'stop_times.txt':[['trip_id','arrival_time','departure_time','stop_id','stop_sequence']]+[[trip,time,time,stop,seq] for trip,stop,time,seq in calls],
        'calendar_dates.txt':[['service_id','date','exception_type']]+(days or [['s','20261231',1],['s','20270101',1]])}
    for name,rows in values.items():
        stream=io.StringIO();csv.writer(stream).writerows(rows);tables[name]=stream.getvalue().encode()
    with zipfile.ZipFile(path,'w') as z:
        for name,value in tables.items():z.writestr(name,value)
    return import_gtfs(path,store,{'source':'simulated'})


@pytest.mark.parametrize('dep,arr,expected',[
    ('18:00:00','30:00:00',1),('01:00:00','08:30:00',1),('01:00:01','08:30:01',0),('02:00:00','09:30:00',0)])
def test_first_sleeper_has_no_earliest_but_keeps_latest(store,tmp_path,dep,arr,expected):
    snap=build(store,tmp_path,[('t','Y','s','')],[('t','a',dep,1),('t','c',arr,2)])
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1));search(store,run)
    assert query(store,run)[1]==expected


def test_wrapped_target_departure_must_fit_allowed():
    Request('a','c').validate()
    assert window_contains(1140,60,10,30)
    assert not window_contains(1140,60,1170,120)
    with pytest.raises(ValueError,match='舒适发车'):
        Request('a','c',depart_start=21*60).validate()


def test_through_prefix_keeps_dates_without_last_continuation(store,tmp_path):
    trips=[('a1','Y1','s','block'),('b1','Y2','s','block'),('c1','Y3','tue','block')]
    calls=[('a1','a','20:00:00',1),('a1','b','26:00:00',2),('b1','b','26:00:00',1),('b1','c','32:00:00',2),('c1','c','32:00:00',1),('c1','d','33:00:00',2)]
    days=[['s','20261231',1],['s','20270101',1],['tue','20270101',1]]
    snap=build(store,tmp_path,trips,calls,days)
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1));search(store,run)
    rows,total,_=query(store,run)
    assert total==1
    p=json.loads(rows[0]['payload'])
    assert p['dates']==['2026-12-31','2027-01-01']
    assert p['legs'][0]['through_trips']==['a1','b1']
    assert p['legs'][0]['train_night'] is None
    long=new_run(store,snap,Request('a','d',min_nights=1,max_nights=1));search(store,long)
    rows,total,_=query(store,long)
    assert total==1 and json.loads(rows[0]['payload'])['dates']==['2027-01-01']


def test_first_night_filter_uses_first_sleeper_not_short_high_speed(store,tmp_path):
    trips=[('short','D1','s',''),('night','Y2','s','')]
    calls=[('short','a','17:59:00',1),('short','b','18:19:00',2),('night','d','20:00:00',1),('night','b','44:12:00',2),('night','c','54:48:00',3)]
    snap=build(store,tmp_path,trips,calls)
    run=new_run(store,snap,Request('a','c',allow_short_hsr=True,min_nights=1,max_nights=1));search(store,run)
    rows,total,_=query(store,run)
    assert total==1
    legs=json.loads(rows[0]['payload'])['legs']
    assert legs[0]['short'] and legs[0]['train_night'] is None
    assert legs[1]['train_night']==2
    assert query(store,run,{'first_night':1})[1]==0
    assert query(store,run,{'first_night':2})[1]==1


def test_task_freezes_mapping_and_evidence_before_first_execution(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,required_cities=['甲城']))
    store.set_setting('city_mapping:'+snapshot,{'a':'changed after queueing'})
    search(store,run)
    assert query(store,run)[1]==2
    checkpoint=json.loads(store.rows('SELECT checkpoint FROM runs WHERE id=?',(run,))[0]['checkpoint'])
    assert checkpoint['mappings']=={} and checkpoint['evidence']==[]


def test_expanded_range_is_rejected_by_query_and_export(store,snapshot,tmp_path):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    for filters in [{'max_nights':3},{'min_nights':1},{'budget':1000.01}]:
        with pytest.raises(ValueError,match='范围未计算'):query(store,run,filters)
        with pytest.raises(ValueError,match='范围未计算'):export_run(store,run,tmp_path,filters,'当前筛选')
    assert query(store,run,{'ids':[]})[1]==0
    assert not list(tmp_path.glob('*.partial'))


def price_payload():
    return {'dates':['2026-12-31'],'legs':[
        {'trip':'t1','from':'a','to':'b','dep':20*3600,'short':False,'unknown_cities':False,'conditional':False,'interpolated':False},
        {'trip':'t2','from':'b','to':'c','dep':44*3600,'short':False,'unknown_cities':False,'conditional':False,'interpolated':False}]}


def fares(seat='硬卧',prices=(.1,.2)):
    return [dict(kind='fare',trip=t,**{'from':a,'to':b},date=d,passenger='student',seat=seat,berth='上',low=p,high=p)
            for t,a,b,d,p in [('t1','a','b','2026-12-31',prices[0]),('t2','b','c','2027-01-01',prices[1])]]


def test_seated_fare_cannot_price_a_sleeper_route_and_money_is_decimal():
    variants,missing=evaluate(price_payload(),fares('硬座'),1)
    assert variants[0]['price_high'] is None and variants[0]['budget_status']=='pending'
    assert variants[0]['legs'][0]['fares']  # Raw evidence preserved.
    variants,missing=evaluate(price_payload(),fares(),.3)
    assert variants[0]['price_high']==.3 and variants[0]['budget_status']=='passed'


@pytest.mark.parametrize('value',[float('inf'),float('nan'),True])
def test_invalid_prices_cannot_break_catalog_json(store,snapshot,tmp_path,value):
    record=dict(fares()[0],source='simulated',observed_at='2026-10-18T12:00:00+08:00',low=value,high=value)
    path=tmp_path/'evidence.json';path.write_text(json.dumps([record]),encoding='utf-8')
    with pytest.raises(ValueError):import_evidence(store,snapshot,path)


@pytest.mark.parametrize('route_type',[3,4,200,700,1000,1100,1200,1500])
def test_known_bus_and_ferry_are_not_unknown_sleeper_candidates(store,tmp_path,route_type):
    snap=build(store,tmp_path,[('t','Y','s','')],[('t','a','20:00:00',1),('t','c','32:00:00',2)],route_type=route_type)
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1));search(store,run)
    assert query(store,run)[1]==0
    audit=json.loads(store.rows('SELECT audit FROM runs WHERE id=?',(run,))[0]['audit'])
    assert audit['preparation']['known_non_rail_transport']>0

def test_interval_crossing_budget_is_pending_not_filtered_out(store,snapshot,tmp_path):
    records=[]
    for trip,first,last in [('t1','a','b'),('t2','b','c'),('t3','b2','c')]:
        for day in ['2026-12-31','2027-01-01','2027-01-02']:
            records.append(dict(kind='fare',source='simulated fixture',observed_at='2026-10-18T12:00:00+08:00',date=day,trip=trip,**{'from':first,'to':last},seat='硬卧',berth='上',passenger='student',low=450,high=550))
    path=tmp_path/'fares.json';path.write_text(json.dumps(records),encoding='utf-8');import_evidence(store,snapshot,path)
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,budget=1000));search(store,run)
    rows,total,_=query(store,run,{'budget':1000})
    assert total==2
    assert all(json.loads(r['payload'])['price_range']==[900,1100] for r in rows)
    assert all(v['budget_status']=='pending' for r in rows for v in json.loads(r['payload'])['date_evidence_variants'])
    assert query(store,run,{'budget':899})[1]==0


def test_queued_run_does_not_adopt_later_fare_evidence(store,snapshot,tmp_path):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    records=[dict(r,source='simulated fixture',observed_at='2026-10-18T12:00:00+08:00') for r in fares()]
    path=tmp_path/'late-fares.json';path.write_text(json.dumps(records),encoding='utf-8');import_evidence(store,snapshot,path)
    search(store,run)
    assert query(store,run,{'known_price':True})[1]==0


@pytest.mark.parametrize('field,value',[('timepoint','2'),('stop_sequence','-1')])
def test_invalid_gtfs_flags_are_rejected(store,tmp_path,field,value):
    path=fixture_zip(tmp_path/'bad-flags.zip')
    with zipfile.ZipFile(path) as z:tables={n:z.read(n) for n in z.namelist()}
    content=tables['stop_times.txt'].decode()
    if field=='timepoint':
        rows=list(csv.reader(io.StringIO(content)));rows[0].append('timepoint')
        for row in rows[1:]:row.append(value)
        stream=io.StringIO();csv.writer(stream).writerows(rows);tables['stop_times.txt']=stream.getvalue().encode()
    else:tables['stop_times.txt']=content.replace(',a,1',',a,-1').encode()
    with zipfile.ZipFile(path,'w') as z:
        for n,v in tables.items():z.writestr(n,v)
    with pytest.raises(ValueError):import_gtfs(path,store,{'source':'simulated'})

def test_through_train_filter_matches_component_without_fuzzy_number(store,tmp_path):
    snap=build(store,tmp_path,[('a1','Y1','s','block'),('b1','Y2','s','block')],[('a1','a','20:00:00',1),('a1','b','26:00:00',2),('b1','b','26:00:00',1),('b1','c','32:00:00',2)])
    run=new_run(store,snap,Request('a','c',min_nights=1,max_nights=1));search(store,run)
    assert query(store,run,{'trains':['Y1']})[1]==1
    assert query(store,run,{'trains':['a1']})[1]==1
    assert query(store,run,{'trains':['Y11']})[1]==0
    assert query(store,run,{'exclude_trains':['Y2']})[1]==0


def test_mapping_import_is_atomic_and_queued_source_is_frozen(store,snapshot,tmp_path):
    from railnomad.data.mapping import import_mapping
    from railnomad.data.release import Cancelled
    path=tmp_path/'cities.csv';path.write_text('stop_id,city,source\na,City-A,simulated source\n',encoding='utf-8')
    with pytest.raises(Cancelled):import_mapping(store,snapshot,path,cancel=lambda:True)
    assert store.setting('city_mapping:'+snapshot,{})=={}
    assert not store.rows("SELECT id FROM evidence WHERE kind='city_membership'")
    assert import_mapping(store,snapshot,path)==1
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    checkpoint=json.loads(store.rows('SELECT checkpoint FROM runs WHERE id=?',(run,))[0]['checkpoint'])
    assert checkpoint['city_mapping_sources'][0]['id']
    assert 'simulated source' in checkpoint['city_mapping_sources'][0]['payload']







def test_first_night_sort_places_unknown_last(store,snapshot):
    from railnomad.storage import encode
    run=new_run(store,snapshot,Request('a','c',min_nights=1,max_nights=1))
    with store.connect() as db:
        for identity,night in [('a-unknown',None),('z-known',1)]:
            payload={'legs':[{'short':False,'train_night':night}]}
            db.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',(run+identity,run,'["A","C"]',1,None,0,0,'pending',encode(payload)))
    rows,_,_=query(store,run,sort='first_night')
    assert rows[0]['id'].endswith('z-known')

@pytest.mark.parametrize('kwargs',[{'min_nights':1.5},{'same_station_gap':True},{'budget':float('nan')},{'budget':float('inf')}])
def test_invalid_request_numbers_are_not_silently_accepted(kwargs):
    with pytest.raises(ValueError):Request('a','c',**kwargs).validate()


def test_optional_none_filters_mean_no_filter(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    assert query(store,run,{'min_nights':None,'max_nights':None,'budget':None})[1]==2

def test_download_rejects_oversize_and_https_downgrade(store,monkeypatch):
    from railnomad.data import release
    from test_data_evidence import Response
    asset={'id':22,'size':3,'browser_download_url':'https://example.invalid/simulated.zip'}
    monkeypatch.setattr(release.urllib.request,'urlopen',lambda *a,**kw:Response(b'abcd'))
    with pytest.raises(ValueError,match='超过 Release'):release.download(asset,store.home)
    class Insecure(Response):
        def geturl(self):return 'http://example.invalid/simulated.zip'
    monkeypatch.setattr(release.urllib.request,'urlopen',lambda *a,**kw:Insecure(b'abc'))
    with pytest.raises(ValueError,match='非 HTTPS'):release.download(asset,store.home)
    assert not list((store.home/'downloads').glob('*.part'))

def test_release_rate_limit_is_retryable_and_honors_server_wait(store,monkeypatch):
    from railnomad.data import release
    import urllib.error
    calls=[]
    monkeypatch.setattr(release.time,'time',lambda:1000)
    def limited(*a,**kw):
        calls.append(1)
        raise urllib.error.HTTPError('https://fixture',429,'fixture',{'Retry-After':'120'},None)
    monkeypatch.setattr(release.urllib.request,'urlopen',limited)
    with pytest.raises(release.RetryLater):release.latest(store)
    with pytest.raises(release.RetryLater):release.latest(store)
    assert len(calls)==1 and store.setting('release_retry_after')==1120


@pytest.mark.parametrize('kind',[1,109,117,401,900,999])
def test_rail_subtypes_and_unknown_modes_are_not_silently_dropped(kind):
    from railnomad.domain.model import transport_category
    assert transport_category(kind) != 'non_rail'
    assert transport_category(999)=='unknown'
