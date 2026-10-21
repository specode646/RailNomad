"""Shared storage must preserve query projections and fully expanded artifacts."""
import json
from pathlib import Path

import pytest

from railnomad.data.release import Cancelled
from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.export import export_run
from railnomad.payloads import pack,unpack
from railnomad.query import query
from railnomad.shared import SharedWriter
from railnomad.storage import Store,encode
from test_requirements_audit import build


def test_shared_leg_context_and_evidence_index_roundtrip(store,tmp_path):
    snap=build(store,tmp_path,[('hsr','D1','s',''),('night','Y2','s','')],
               [('hsr','a','17:00:00',1),('hsr','b','17:20:00',2),('night','b','20:00:00',1),('night','c','32:00:00',2)])
    store.set_setting('city_mapping:'+snap,{'b':'甲城','b2':'甲城'})
    run=new_run(store,snap,Request('甲城','c',allow_short_hsr=True,origin_kind='city',min_nights=1,max_nights=1,target_depart_start=1260,departure_date='2026-12-31'))
    search(store,run)
    rows,total,_=query(store,run)
    assert total==2
    values=[json.loads(row['payload']) for row in rows]
    assert sorted(p['legs'][-1]['departure_relaxation_minutes'] for p in values)==[0,60]
    assert all(v['legs'][-1]['leg']==len(p['legs'])-1 for p in values for v in p['date_evidence_variants'])
    assert query(store,run,{'trains':['D1']})[1]==1
    assert query(store,run,{'exclude_trains':['D1']})[1]==1
    assert query(store,run,{'trains':['Y2']})[1]==2
    assert query(store,run,{'first_night':1})[1]==2
    assert query(store,run,{'origin':'b'})[1]==1
    assert query(store,run,{'target_only':True})[1]==1
    for sort in ('changes','duration','price','nights','daylight','relaxation','first_night','evidence','preference'):
        assert query(store,run,sort=sort)[1]==2
        assert query(store,run,sort=sort,grouped=True)[2]==2
    with store.connect() as db:
        assert db.execute('SELECT typeof(payload) FROM results WHERE run_id=?',(run,)).fetchone()[0]=='blob'
        assert db.execute('SELECT COUNT(*) FROM shared_parts WHERE run_id=?',(run,)).fetchone()[0]>0
    target=export_run(store,run,tmp_path,{},'任务全部')
    exported=[json.loads(line) for line in (Path(target)/'candidates.jsonl').read_text(encoding='utf-8').splitlines()]
    assert {p['id']:p for p in exported}=={p['id']:p for p in values}
    assert '$shared' not in (Path(target)/'candidates.jsonl').read_text(encoding='utf-8')


def test_cancel_flushes_parts_and_resume_preserves_counts(store,snapshot,monkeypatch):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    generated=[0]
    original=SharedWriter.compact
    def count(self,payload):
        generated[0]+=1
        return original(self,payload)
    monkeypatch.setattr(SharedWriter,'compact',count)
    with pytest.raises(Cancelled):search(store,run,cancel=lambda:generated[0]>=1)
    assert query(store,run)[1]==1
    assert json.loads(query(store,run)[0][0]['payload'])['legs']
    search(store,run)
    assert query(store,run)[1]==2
    audit=json.loads(store.rows('SELECT audit FROM runs WHERE id=?',(run,))[0]['audit'])
    assert audit['complete_candidates']==2 and audit['result_states']=={'pending':2}


def test_schema_upgrade_does_not_change_legacy_payload(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    record=store.rows('SELECT * FROM results WHERE run_id=? LIMIT 1',(run,))[0]
    with store.connect() as db:
        db.execute('UPDATE results SET payload=? WHERE id=?',(record['payload'],record['id']))
        db.execute('PRAGMA user_version=1')
    upgraded=Store(store.home)
    assert upgraded.rows('SELECT payload FROM results WHERE id=?',(record['id'],))[0]['payload']==record['payload']
    with upgraded.connect() as db:
        assert db.execute('PRAGMA user_version').fetchone()[0]==2


@pytest.mark.parametrize('bad',[b'bad',pack('{}')+b'junk',b'RN2\0bad zlib'])
def test_invalid_compressed_records_rejected(bad):
    with pytest.raises(ValueError):unpack(bad)


def test_missing_fragment_is_explicit_error(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    with store.connect() as db:db.execute('DELETE FROM shared_parts WHERE run_id=?',(run,))
    store.fragment_cache.clear()
    with pytest.raises(ValueError,match='片段缺失'):query(store,run)


def test_compression_failure_does_not_count_unstored_candidate(store,snapshot,monkeypatch):
    from railnomad import payloads
    monkeypatch.setattr(payloads,'MAX_JSON_BYTES',50)
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    with pytest.raises(ValueError,match='存储保护限制'):search(store,run)
    saved=store.rows('SELECT status,audit FROM runs WHERE id=?',(run,))[0]
    assert saved['status']=='failed'
    assert json.loads(saved['audit'])['complete_candidates']==0
    assert query(store,run)[1]==0


def test_group_totals_preserved_for_empty_requested_page(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    assert query(store,run,grouped=True,page=99)==([],1,2)
    assert query(store,run,grouped=True,page_size=0)==([],1,2)


def test_cancel_recovery_preserves_committed_summary(store,snapshot):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2));search(store,run)
    before=store.rows('SELECT checkpoint FROM runs WHERE id=?',(run,))[0]['checkpoint']
    with store.connect() as db:db.execute("UPDATE runs SET status='cancelled' WHERE id=?",(run,))
    with pytest.raises(Cancelled):search(store,run,cancel=lambda:True)
    saved=store.rows('SELECT status,audit,checkpoint FROM runs WHERE id=?',(run,))[0]
    assert saved['status']=='cancelled' and saved['checkpoint']==before
    assert json.loads(saved['audit'])['complete_candidates']==2
    search(store,run)
    assert query(store,run)[1]==2
