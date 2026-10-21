import io
import json
import urllib.error
import zipfile
import pytest
from railnomad.data import release
from railnomad.data.gtfs import import_gtfs
from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.evidence import import_evidence
from railnomad.query import query
from conftest import fixture_zip


class Response(io.BytesIO):
    def __init__(self,value,headers=None):super().__init__(value);self.headers=headers or {}


def test_release_conditional_cache_and_prerelease(store,monkeypatch):
    payload={'draft':False,'prerelease':False,'tag_name':'simulated','assets':[{'name':'data.zip'}]}
    monkeypatch.setattr(release.urllib.request,'urlopen',lambda *a,**kw:Response(json.dumps(payload).encode(),{'ETag':'fixture'}))
    assert release.latest(store)['release']['tag_name']=='simulated'
    def unchanged(*a,**kw):raise urllib.error.HTTPError('https://fixture',304,'unchanged',{},None)
    monkeypatch.setattr(release.urllib.request,'urlopen',unchanged)
    assert release.latest(store)['release']['tag_name']=='simulated'
    payload['prerelease']=True
    monkeypatch.setattr(release.urllib.request,'urlopen',lambda *a,**kw:Response(json.dumps(payload).encode()))
    with pytest.raises(ValueError):release.latest(store)


def test_download_mismatch_and_cancel_keep_snapshot(store,snapshot,monkeypatch):
    asset={'id':1,'size':3,'browser_download_url':'https://example.invalid/simulated.zip','digest':'sha256:wrong'}
    monkeypatch.setattr(release.urllib.request,'urlopen',lambda *a,**kw:Response(b'abc'))
    with pytest.raises(ValueError):release.download(asset,store.home)
    assert not (store.home/'downloads'/'asset-1.zip').exists()
    assert store.snapshot()['id']==snapshot
    with pytest.raises(release.Cancelled):release.download(asset,store.home,cancel=lambda:True)
    assert not list((store.home/'downloads').glob('*.part'))


def test_manual_date_specific_evidence_and_frozen_tasks(store,snapshot,tmp_path):
    old=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,old)
    assert query(store,old,{'known_price':True})[1]==0
    records=[]
    for trip,start,end in [('t1','a','b'),('t2','b','c'),('t3','b2','c')]:
        for day in ['2026-12-31','2027-01-01','2027-01-02']:
            record={'trip':trip,'from':start,'to':end,'date':day,'source':'simulated manual fixture','observed_at':'2026-10-18T12:00:00+08:00'}
            records.append(dict(record,kind='fare',seat='simulated sleeper',seat_kind='sleeper',berth='upper',passenger='student',low=100,high=120))
            records.append(dict(record,kind='sleeper',status='passed'))
            records.append(dict(record,kind='track',status='passed'))
    path=tmp_path/'manual.json';path.write_text(json.dumps(records),encoding='utf-8');import_evidence(store,snapshot,path)
    new=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,new)
    assert query(store,new,{'known_price':True})[1]==2
    assert {r['price'] for r in query(store,new)[0]}=={240}
    assert query(store,old,{'known_price':True})[1]==0
    # Task rerun uses frozen evidence rather than current catalog.
    with store.connect() as db:db.execute('DELETE FROM evidence')
    assert query(store,new,{'known_price':True})[1]==2


def test_regular_calendar_and_exception(store,tmp_path):
    path=fixture_zip(tmp_path/'regular.zip')
    with zipfile.ZipFile(path,'a') as z:
        z.writestr('calendar.txt','service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\ns3,1,0,0,0,0,0,0,20261228,20270104\n')
    identity=import_gtfs(path,store,{'source':'simulated'})
    import sqlite3
    db=sqlite3.connect(store.snapshot(identity)['path'])
    assert list(db.execute("SELECT day FROM days WHERE service='s3' ORDER BY day"))==[('2026-12-28',),('2027-01-04',)]
    db.close()

def test_through_block_is_one_ride(store,tmp_path):
    path=fixture_zip(tmp_path/'through.zip')
    # Separate simulated feed with a same-vehicle number change at 02:00.
    import io,csv
    with zipfile.ZipFile(path) as original:
        tables={n:original.read(n) for n in original.namelist()}
    tables['trips.txt']=b'route_id,service_id,trip_id,trip_short_name,block_id\nr,s1,t1,Y1,through\nr,s1,t2,Y2,through\n'
    tables['stop_times.txt']=b'trip_id,arrival_time,departure_time,stop_id,stop_sequence\nt1,20:00:00,20:00:00,a,1\nt1,26:00:00,26:00:00,b,2\nt2,26:00:00,26:00:00,b,1\nt2,32:00:00,32:00:00,c,2\n'
    with zipfile.ZipFile(path,'w') as z:
        for n,value in tables.items():z.writestr(n,value)
    identity=import_gtfs(path,store,{'source':'simulated'})
    run=new_run(store,identity,Request('a','c',min_nights=1,max_nights=1,allow_short_hsr=False));search(store,run)
    rows,total,_=query(store,run)
    assert total==1
    p=json.loads(rows[0]['payload'])
    assert len(p['legs'])==1
    assert p['legs'][0]['through_trips']==['t1','t2']

def test_sleeper_450_boundary_and_pickup_restriction(store,tmp_path):
    def build(minutes,pickup):
        path=fixture_zip(tmp_path/f'seconds-{minutes}-{pickup}.zip')
        with zipfile.ZipFile(path) as z:tables={n:z.read(n) for n in z.namelist()}
        arr=22*3600+30*60+minutes*60
        tables['stop_times.txt']=(f'trip_id,arrival_time,departure_time,stop_id,stop_sequence,pickup_type\nt1,22:30:00,22:30:00,a,1,{pickup}\nt1,{arr//3600:02d}:{arr//60%60:02d}:00,{arr//3600:02d}:{arr//60%60:02d}:00,c,2,0\n').encode()
        # Keep all trips declared, but only t1 has calls in this boundary fixture.
        with zipfile.ZipFile(path,'w') as z:
            for n,v in tables.items():z.writestr(n,v)
        identity=import_gtfs(path,store,{'source':'simulated'})
        run=new_run(store,identity,Request('a','c',min_nights=1,max_nights=1,allow_short_hsr=False));search(store,run)
        return query(store,run)[1]
    assert build(450,0)==1
    assert build(449,0)==0
    assert build(450,1)==0


def test_four_and_five_nights_not_capped(store,tmp_path):
    path=fixture_zip(tmp_path/'five.zip')
    with zipfile.ZipFile(path) as z:tables={n:z.read(n) for n in z.namelist()}
    tables['stops.txt']=('stop_id,stop_name,stop_lat,stop_lon,city\n'+'\n'.join(f's{i},SIMULATED-{i},30,110,C{i}' for i in range(6))).encode()
    tables['trips.txt']=('route_id,service_id,trip_id,trip_short_name\n'+'\n'.join(f'r,s1,t{i},Y{i}' for i in range(5))).encode()
    tables['stop_times.txt']=('trip_id,arrival_time,departure_time,stop_id,stop_sequence\n'+'\n'.join(f't{i},20:00:00,20:00:00,s{i},1\nt{i},32:00:00,32:00:00,s{i+1},2' for i in range(5))).encode()
    tables['calendar_dates.txt']=b'service_id,date,exception_type\ns1,20261229,1\ns1,20261230,1\ns1,20261231,1\ns1,20270101,1\ns1,20270102,1\n'
    with zipfile.ZipFile(path,'w') as z:
        for n,v in tables.items():z.writestr(n,v)
    identity=import_gtfs(path,store,{'source':'simulated'})
    run=new_run(store,identity,Request('s0','s5',min_nights=5,max_nights=5));search(store,run)
    rows,total,_=query(store,run)
    assert total==1 and rows[0]['nights']==5
