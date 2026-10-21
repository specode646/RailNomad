import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from conftest import fixture_zip
from railnomad.data.gtfs import import_gtfs,minutes


def import_calls(store,tmp_path,rows):
    source=fixture_zip(tmp_path/'midnight.zip')
    with zipfile.ZipFile(source) as archive:tables={n:archive.read(n) for n in archive.namelist()}
    tables['stops.txt']+=b'd,SIMULATED-D,33,113,D-city\n'
    tables['trips.txt']=b'route_id,service_id,trip_id,trip_short_name\nr,s1,t1,K129\n'
    tables['stop_times.txt']=('trip_id,arrival_time,departure_time,stop_id,stop_sequence\n'+'\n'.join(f't1,{arr},{dep},{stop},{i+1}' for i,(stop,arr,dep) in enumerate(rows))).encode()
    with zipfile.ZipFile(source,'w') as archive:
        for name,value in tables.items():archive.writestr(name,value)
    identity=import_gtfs(source,store,{'source':'simulated'})
    return store.snapshot(identity),source


def test_k129_midnight_day_offset_is_repaired_with_complete_provenance(store,tmp_path):
    snap,source=import_calls(store,tmp_path,[('a','22:50:00','22:54:00'),('b','47:56:00','24:08:00'),('c','30:27:00','30:27:00')])
    quality=json.loads(snap['quality']);assert quality['repaired_trips']==['t1']
    assert quality['quarantined_trips']==[] and quality['coverage_complete']
    assert quality['original_time_anomalies'][0]['arrival_time']=='47:56:00'
    with sqlite3.connect(snap['path']) as db:
        assert db.execute("SELECT arr,dep FROM calls WHERE stop='b'").fetchone()==(minutes('23:56:00'),minutes('24:08:00'))
        assert db.execute('SELECT original_arr,original_dep,arr,dep FROM timetable_repairs').fetchone()==(minutes('47:56:00'),minutes('24:08:00'),minutes('23:56:00'),minutes('24:08:00'))
    assert (Path(snap['path']).parent/'source.zip').read_bytes()==source.read_bytes()
    assert import_gtfs(source,store)==snap['id']


@pytest.mark.parametrize('previous,arrival,departure,following',[
    ('24:01:00','47:56:00','24:08:00','30:27:00'), # correction would precede previous stop
    ('22:54:00','47:56:00','24:08:00','24:01:00'), # downstream remains non-monotonic
    ('35:55:00','37:06:00','37:00:00','40:00:00'), # minutes of inversion, not a day offset
    ('20:00:00','21:01:00','19:48:00','25:00:00'),
    ('22:00:00','23:56:00','24:08:00','30:27:00'), # valid midnight call is unchanged
])
def test_other_errors_are_not_guessed_and_valid_midnight_is_unchanged(store,tmp_path,previous,arrival,departure,following):
    snap,_=import_calls(store,tmp_path,[('a',previous,previous),('b',arrival,departure),('c',following,following)])
    quality=json.loads(snap['quality']);assert not quality['repaired_records']
    valid=(arrival=='23:56:00')
    assert quality['quarantined_trips']==[]
    assert quality['time_affected_trips']==([] if valid else ['t1'])


def test_trip_repairs_are_atomic_if_another_error_remains(store,tmp_path):
    snap,_=import_calls(store,tmp_path,[('a','22:54:00','22:54:00'),('b','47:56:00','24:08:00'),('c','30:27:00','30:20:00')])
    quality=json.loads(snap['quality']);assert quality['time_affected_trips']==['t1']
    assert quality['repaired_records']==[]


def test_new_importer_preserves_old_snapshot_and_same_source_evidence(store,tmp_path,monkeypatch):
    from railnomad.data import gtfs
    from railnomad.storage import encode
    snap,source=import_calls(store,tmp_path,[('a','22:54:00','22:54:00'),('b','47:56:00','24:08:00'),('c','30:27:00','30:27:00')])
    original=Path(snap['path']).read_bytes()
    store.set_setting('city_mapping:'+snap['id'],{'a':'Test City'})
    with store.connect() as db:db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',('old-evidence',snap['id'],'fare',encode({'kind':'fare','source':'simulated'}),'2026-10-18'))
    monkeypatch.setattr(gtfs,'IMPORTER_VERSION','simulated-next-parser')
    identity=gtfs.import_gtfs(source,store)
    assert identity!=snap['id'] and len(store.snapshots())==2
    assert Path(snap['path']).read_bytes()==original
    assert store.setting('city_mapping:'+identity)=={'a':'Test City'}
    assert len(store.rows('SELECT * FROM evidence WHERE snapshot=?',(identity,)))==1
    assert json.loads(store.snapshot(identity)['metadata'])['same_source_predecessors']==[snap['id']]
