import json,sqlite3
from pathlib import Path
from railnomad.data.mapping import import_mapping,inherit_mapping
from railnomad.data.gtfs import import_gtfs
from railnomad.storage import encode
from conftest import fixture_zip


def mapped(store,snapshot,tmp_path):
    csv=tmp_path/'cities.csv';csv.write_text('stop_id,city,source\na,甲市,verified-source\nb,乙市,verified-source\n',encoding='utf-8')
    import_mapping(store,snapshot,csv)


def test_changed_gtfs_inherits_identical_stations_and_provenance_only(store,snapshot,tmp_path):
    mapped(store,snapshot,tmp_path)
    with store.connect() as db:db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',('dated',snapshot,'fare','{}','2026-01-01'))
    target=import_gtfs(fixture_zip(tmp_path/'new.zip',second_date='20270102'),store)
    assert store.setting('city_mapping:'+target)=={'a':'甲市','b':'乙市'}
    evidence=store.rows('SELECT kind,payload FROM evidence WHERE snapshot=?',(target,))
    assert len(evidence)==1 and evidence[0]['kind']=='city_membership'
    rows=json.loads(evidence[0]['payload']);assert all(r['source']=='verified-source' and r['inherited_from']==snapshot for r in rows)
    assert inherit_mapping(store,target)==0
    assert len(store.rows('SELECT id FROM evidence WHERE snapshot=?',(target,)))==1


def test_coordinate_or_name_change_missing_provenance_and_manual_override(store,snapshot,tmp_path):
    mapped(store,snapshot,tmp_path)
    target=import_gtfs(fixture_zip(tmp_path/'new.zip',second_date='20270102'),store)
    # Simulate a legacy imported snapshot that predates automatic inheritance.
    with store.connect() as db:
        db.execute('DELETE FROM settings WHERE key=?',('city_mapping:'+target,))
        db.execute('DELETE FROM evidence WHERE snapshot=?',(target,))
    with sqlite3.connect(store.snapshot(target)['path']) as db:db.execute("UPDATE stops SET lat=lat+1 WHERE id='b'")
    assert inherit_mapping(store,target)==1
    assert store.setting('city_mapping:'+target)=={'a':'甲市'}
    store.set_setting('city_mapping:'+target,{'a':'手工覆盖'})
    assert inherit_mapping(store,target)==0
    assert store.setting('city_mapping:'+target)=={'a':'手工覆盖'}
    assert store.setting('city_mapping:'+snapshot)=={'a':'甲市','b':'乙市'}


def test_activate_repairs_previously_imported_snapshot(store,snapshot,tmp_path):
    target=import_gtfs(fixture_zip(tmp_path/'new.zip',second_date='20270102'),store)
    mapped(store,snapshot,tmp_path)
    store.activate(target)
    assert store.setting('active_snapshot')==target
    assert store.setting('city_mapping:'+target)=={'a':'甲市','b':'乙市'}


def test_no_provenance_and_same_id_with_different_name_are_not_reused(store,snapshot,tmp_path):
    store.set_setting('city_mapping:'+snapshot,{'a':'无来源'})
    target=import_gtfs(fixture_zip(tmp_path/'new.zip',second_date='20270102'),store)
    assert not store.setting('city_mapping:'+target,{})
    mapped(store,snapshot,tmp_path)
    with sqlite3.connect(store.snapshot(target)['path']) as db:db.execute("UPDATE stops SET name='另一个车站' WHERE id='a'")
    assert inherit_mapping(store,target)==1
    assert store.setting('city_mapping:'+target)=={'b':'乙市'}
