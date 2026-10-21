import json
import zipfile
from datetime import date
import pytest
from railnomad.data.gtfs import import_gtfs,minutes
from railnomad.data.release import Cancelled
from railnomad.domain.model import Request,short_window,daylight
from railnomad.domain.search import new_run,search
from railnomad.query import query
from railnomad.export import export_run,safe
from conftest import fixture_zip


def test_time_offsets_and_boundaries():
    assert minutes('49:00:01')==176401
    with pytest.raises(ValueError):minutes('24:60:00')
    r=Request('a','c',limit_daylight=True,first_daylight_limit=180,last_daylight_limit=180)
    assert short_window(390*60,450*60,r)
    assert short_window(23*3600+30*60,86400+30*60,r)
    assert not short_window(23*3600+31*60,86400+31*60,r)
    assert not short_window(389*60,400*60,r)
    r.target_arrive_start=480;r.target_depart_end=1200
    assert short_window(480*60,540*60,r)
    assert not short_window(23*3600,24*3600,r)
    assert daylight([{'dep':7*3600,'arr':10*3600}],r)[1]
    assert not daylight([{'dep':7*3600,'arr':10*3600+1}],r)[1]


def test_import_idempotent_and_atomic(store,snapshot,tmp_path):
    assert import_gtfs(fixture_zip(tmp_path/'same.zip'),store,{'source':'simulated'})==snapshot
    assert len(store.snapshots())==1
    unsafe=tmp_path/'unsafe.zip'
    with zipfile.ZipFile(unsafe,'w') as z:z.writestr('../escape','bad')
    with pytest.raises(ValueError):import_gtfs(unsafe,store)
    assert store.snapshot()['id']==snapshot
    with pytest.raises(ValueError):import_gtfs(tmp_path/'same.zip',store,{'digest':'sha256:incorrect'})


def test_quarantine_preserves_source(store,tmp_path):
    identity=import_gtfs(fixture_zip(tmp_path/'bad.zip',invalid=True),store,{'source':'simulated'})
    snap=store.snapshot(identity);quality=json.loads(snap['quality'])
    assert quality['quarantined_trips']==[]
    assert quality['time_affected_trips']==['t2']
    assert not quality['coverage_complete']
    from pathlib import Path
    assert (Path(snap['path']).parent/'source.zip').exists()


def test_calendar_variants_against_absolute_oracle(store,snapshot,tmp_path):
    request=Request('a','c',min_nights=2,max_nights=2)
    run=new_run(store,snapshot,request);result=search(store,run)
    assert result['status']=='completed'
    rows,total,_=query(store,run)
    assert total==2  # Same-station and same-city station alternative both retained.
    # Independent finite absolute date-instance oracle for this SIMULATED graph:
    # every first service date pairs with a second calendar day, subject to 10/11h.
    first_dates=[date(2026,12,31),date(2027,1,1)]
    second_dates=[date(2027,1,1),date(2027,1,2)]
    oracle=set()
    for first in first_dates:
        for second in second_dates:
            depart1=first.toordinal()*86400+20*3600
            arrive1=first.toordinal()*86400+32*3600
            depart2=second.toordinal()*86400+20*3600
            if second.toordinal()-first.toordinal()!=1:continue
            for station,gap in [('b',600),('b2',660)]:
                if depart2-arrive1>=gap*60:oracle.add((station,first.isoformat()))
    actual={(json.loads(row['payload'])['legs'][1]['from'],d) for row in rows for d in json.loads(row['payload'])['dates']}
    assert actual==oracle
    assert all(json.loads(r['payload'])['known_price'] is None for r in rows)
    assert query(store,run,{'cities':['甲城','丙城']})[1]==2
    assert query(store,run,{'ordered_cities':['丙城','甲城']})[1]==0
    assert query(store,run,{'known_price':True})[1]==0
    # Global filter applies before pagination, even on page 2.
    page1=query(store,run,page=0,page_size=1)[0]
    page2=query(store,run,page=1,page_size=1)[0]
    assert len({page1[0]['id'],page2[0]['id']})==2
    out=export_run(store,run,tmp_path,{},'任务全部')
    from pathlib import Path
    assert len((Path(out)/'candidates.jsonl').read_text(encoding='utf-8').splitlines())==2
    assert safe(' =SUM(1,2)').startswith("'")


def test_cancel_resume_no_duplicate(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    calls=[0]
    def cancel():
        calls[0]+=1
        return calls[0]>2
    with pytest.raises(Cancelled):search(store,run,cancel=cancel)
    assert store.rows('SELECT status FROM runs WHERE id=?',(run,))[0]['status']=='cancelled'
    search(store,run)
    assert query(store,run)[1]==2
    search(store,run)
    assert query(store,run)[1]==2


def test_disjoint_calendars_never_spliced(store,tmp_path):
    identity=import_gtfs(fixture_zip(tmp_path/'dates.zip',second_date='20261215'),store,{'source':'simulated'})
    run=new_run(store,identity,Request('a','c',min_nights=2,max_nights=2))
    search(store,run)
    for row in query(store,run)[0]:
        assert json.loads(row['payload'])['dates']==['2027-01-01']

def test_large_catalog_filters_before_paging(store,snapshot):
    from railnomad.storage import encode
    from railnomad.domain.search import new_run
    from railnomad.domain.model import Request
    from railnomad.query import query
    run=new_run(store,snapshot,Request('a','c'))
    rows=[]
    for i in range(10050):
        p={'pattern':['A','B' if i<10049 else 'TARGET'],'legs':[], 'relaxation_minutes':i%5, 'min_daylight_off_minutes':700}
        rows.append((f'{run}-{i:05d}',run,encode(p['pattern']),3,None,i%2,0,'pending',encode(p)))
    with store.connect() as db:db.executemany('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',rows)
    matches,total,_=query(store,run,{'cities':['TARGET']},page_size=10)
    assert total==1 and matches[0]['id'].endswith('10049')
    assert query(store,run,page=334,page_size=30)[1]==10050
    assert query(store,run,{'cities':['A','TARGET'],'city_mode':'AND'})[1]==1
