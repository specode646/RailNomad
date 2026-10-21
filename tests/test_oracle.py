"""Independent finite actual-instance oracle over seeded SIMULATED feeds."""
import csv,io,json,random,zipfile
from datetime import date
import pytest
from railnomad.data.gtfs import import_gtfs
from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.query import query


@pytest.mark.parametrize('limit_daylight',[False,True])
@pytest.mark.parametrize('endpoint_limit',[None,90,180])
@pytest.mark.parametrize('seed',range(6))
@pytest.mark.parametrize('departure_date',[None,'2026-12-31'])
def test_random_calendar_compression_matches_instances(store,tmp_path,seed,departure_date,limit_daylight,endpoint_limit):
    rng=random.Random(seed)
    service_dates=[date(2026,12,30).toordinal()+i for i in range(5)]
    trains=[]
    for k in range(10):
        a=rng.randrange(3);b=rng.randrange(a+1,4)
        dep=rng.choice([19*3600,20*3600,1800])+rng.choice([0,86400,172800])
        duration=rng.choice([449,450,660,720,780])*60
        days={d for d in service_dates if rng.random()<.75}
        trains.append(dict(id=f't{k}',a=str(a),b=str(b),dep=dep,arr=dep+duration,days=days))
    tables={
        'agency.txt':[['agency_name','agency_url','agency_timezone'],['SIMULATED','https://example.invalid','Asia/Shanghai']],
        'stops.txt':[['stop_id','stop_name','stop_lat','stop_lon','city']]+[[str(i),f'SIMULATED-{i}',30+i,110+i,f'C{i}'] for i in range(4)],
        'routes.txt':[['route_id','route_type'],['r',2]],
        'trips.txt':[['route_id','service_id','trip_id','trip_short_name']]+[['r',t['id'],t['id'],t['id']] for t in trains],
        'stop_times.txt':[['trip_id','arrival_time','departure_time','stop_id','stop_sequence']],
        'calendar_dates.txt':[['service_id','date','exception_type']]}
    def fmt(t):return f'{t//3600:02d}:{t//60%60:02d}:{t%60:02d}'
    for t in trains:
        tables['stop_times.txt'] += [[t['id'],fmt(t['dep']),fmt(t['dep']),t['a'],1],[t['id'],fmt(t['arr']),fmt(t['arr']),t['b'],2]]
        tables['calendar_dates.txt'] += [[t['id'],date.fromordinal(d).strftime('%Y%m%d'),1] for d in sorted(t['days'])]
    path=tmp_path/'random-simulated.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,rows in tables.items():
            s=io.StringIO();csv.writer(s).writerows(rows);z.writestr(name,s.getvalue())
    identity=import_gtfs(path,store,{'source':'simulated'})
    run=new_run(store,identity,Request('0','3',min_nights=1,max_nights=3,allow_short_hsr=False,departure_date=departure_date,limit_daylight=limit_daylight,first_daylight_limit=endpoint_limit,last_daylight_limit=endpoint_limit))
    search(store,run)
    actual=set()
    for row in query(store,run,page_size=100000)[0]:
        p=json.loads(row['payload'])
        for d in p['dates']:actual.add((tuple((l['trip'],l['from'],l['to'],l['dep'],l['arr']) for l in p['legs']),date.fromisoformat(d).toordinal()))
    instances=[]
    for t in trains:
        for d in t['days']:instances.append(dict(t,dep=d*86400+t['dep'],arr=d*86400+t['arr']))
    expected=set()
    def walk(path,nights,daylight):
        last=path[-1]
        if last['b']=='3':
            if 1<=len(nights)<=3:
                first_day=path[0]['dep']//86400
                expected.add((tuple((l['id'],l['a'],l['b'],l['dep']-first_day*86400,l['arr']-first_day*86400) for l in path),first_day))
            return
        for nxt in instances:
            if nxt['a']==last['b'] and nxt['dep']-last['arr']>=600*60:
                add(path,nxt,nights,daylight)
    def add(path,t,nights,old_daylight):
        if t['arr']-t['dep']<450*60:return
        clock=t['dep']%86400
        if path and not(clock>=19*3600 or clock<=3600):return
        if t['arr']%86400<6*3600:return
        if t['b']!='3' and t['arr']%86400>10*3600:return
        new_nights=set(nights)
        for day in range(t['dep']//86400,t['arr']//86400+1):
            if max(t['dep'],day*86400)<min(t['arr'],day*86400+6*3600):new_nights.add(day-1)
        if new_nights==nights or len(new_nights)>3 or len(new_nights)!=max(new_nights)-min(new_nights)+1:return
        daytime=dict(old_daylight)
        for day in range(t['dep']//86400,t['arr']//86400+1):
            amount=max(0,min(t['arr'],day*86400+20*3600)-max(t['dep'],day*86400+7*3600))
            daytime[day]=daytime.get(day,0)+amount
        if limit_daylight:
            start_day=(path[0] if path else t)['dep']//86400;end_day=t['arr']//86400
            for day,amount in daytime.items():
                if day==start_day and endpoint_limit is not None and amount>endpoint_limit*60:return
                if t['b']=='3' and day==end_day and endpoint_limit is not None and amount>endpoint_limit*60:return
                if day!=start_day and (day<end_day or t['b']=='3' and day!=end_day) and amount>180*60:return
        walk(path+[t],new_nights,daytime)
    for t in instances:
        if t['a']=='0':add([],t,set(),{})
    if departure_date:
        expected={item for item in expected if item[1]==date.fromisoformat(departure_date).toordinal()}
    assert actual==expected
