import json,sqlite3
import pytest
from railnomad.deletion import delete,preview
from railnomad.domain.model import Request
from railnomad.domain.search import new_run,search
from railnomad.data.release import Cancelled
from railnomad.shared import SharedWriter
from railnomad.storage import encode,now
from railnomad.query import query
from railnomad.fares import save_assessment,sync_run_fares
from test_fares_and_cities import candidate,assessment


def foreign_keys(store):
    with store.connect() as db:assert not db.execute('PRAGMA foreign_key_check').fetchall()


def test_single_delete_cleans_dependencies_preserves_siblings_and_shared_quotes(store,snapshot):
    run,p=candidate(store,snapshot);other,other_p=candidate(store,snapshot)
    quotes=assessment(p,[80,80])
    for detail in quotes['dates'][p['dates'][0]]['legs']:detail['fares'][0]['observed_at']='2026-10-19T10:00:00+08:00'
    save_assessment(store,p,quotes);sync_run_fares(store,run)
    with store.connect() as db:db.execute('INSERT INTO favorites VALUES(?,?)',(p['id'],now()))
    evidence=store.rows('SELECT * FROM evidence ORDER BY id');parts=store.rows('SELECT * FROM shared_parts WHERE run_id=?',(run,))
    before=preview(store,'results',[run],[p['id']]);assert before['deleted_results']==1
    result=delete(store,'results',[run],[p['id']]);assert result['deleted_results']==1
    assert query(store,run)[1]==1 and query(store,other)[1]==2
    for table,column in [('favorites','result_id'),('fare_assessments','result_id'),('fare_dependency_results','result_id')]:
        assert not store.rows('SELECT 1 FROM '+table+' WHERE '+column+'=?',(p['id'],))
    assert store.rows('SELECT * FROM evidence ORDER BY id')==evidence
    assert store.rows('SELECT * FROM shared_parts WHERE run_id=?',(run,))==parts
    audit=json.loads(store.rows('SELECT audit FROM runs WHERE id=?',(run,))[0]['audit'])
    assert audit['complete_candidates']==1 and audit['city_combinations']==1 and sum(audit['result_states'].values())==1
    assert sync_run_fares(store,run)['unchanged']
    assert delete(store,'results',[run],[p['id']])['deleted_results']==0
    with pytest.raises(sqlite3.IntegrityError):save_assessment(store,p,quotes)
    foreign_keys(store)


def test_group_delete_includes_all_variants_but_no_other_task(store,snapshot):
    run,p=candidate(store,snapshot);other,_=candidate(store,snapshot)
    pattern=store.rows('SELECT pattern FROM results WHERE id=?',(p['id'],))[0]['pattern']
    assert preview(store,'patterns',[run],patterns=[pattern])['deleted_results']==2
    delete(store,'patterns',[run],patterns=[pattern])
    assert query(store,run)[1]==0 and query(store,other)[1]==2
    assert store.rows('SELECT * FROM runs WHERE id=?',(run,))
    assert len(store.rows('SELECT * FROM deleted_patterns WHERE run_id=?',(run,)))==1
    foreign_keys(store)


def test_task_batch_delete_removes_owned_records_and_retains_exported_files(store,snapshot,tmp_path):
    run,p=candidate(store,snapshot);other,_=candidate(store,snapshot)
    queued=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2))
    path=tmp_path/'historical-export.txt';path.write_text('historical output',encoding='utf-8')
    with store.connect() as db:
        db.execute('INSERT INTO favorites VALUES(?,?)',(p['id'],now()))
        db.execute('INSERT INTO exports(run_id,scope,path,created) VALUES(?,?,?,?)',(run,'all',str(path),now()))
    result=delete(store,'tasks',[run,queued]);assert result['deleted_runs']==2 and result['deleted_results']==2
    for table,column in [('runs','id'),('results','run_id'),('shared_parts','run_id'),('exports','run_id'),('fare_index_state','run_id'),('fare_dependency_runs','run_id')]:
        assert not store.rows('SELECT 1 FROM '+table+' WHERE '+column+'=?',(run,))
    assert path.read_text(encoding='utf-8')=='historical output' and store.snapshot()['id']==snapshot
    assert query(store,other)[1]==2
    foreign_keys(store)


@pytest.mark.parametrize('scope',['tasks','patterns','results'])
def test_cancel_rolls_back_every_record_and_exclusion(store,snapshot,scope):
    run,p=candidate(store,snapshot)
    pattern=store.rows('SELECT pattern FROM results WHERE id=?',(p['id'],))[0]['pattern']
    before=store.rows('SELECT * FROM runs');cancelled=[False]
    with pytest.raises(Cancelled):
        delete(store,scope,[run],[p['id']],[pattern],progress=lambda **_:cancelled.__setitem__(0,True),cancel=lambda:cancelled[0])
    assert query(store,run)[1]==2 and store.rows('SELECT * FROM runs')==before
    assert not store.rows('SELECT * FROM deleted_results') and not store.rows('SELECT * FROM deleted_patterns')
    assert store.setting('catalog_revision',0)==0
    foreign_keys(store)


@pytest.mark.parametrize('scope,remaining',[('results',1),('patterns',0)])
def test_resume_never_restores_deleted_results_or_groups(store,snapshot,monkeypatch,scope,remaining):
    run=new_run(store,snapshot,Request('a','c',min_nights=2,max_nights=2,departure_date='2026-12-31'))
    generated=[0];original=SharedWriter.compact
    def count(self,p):generated[0]+=1;return original(self,p)
    monkeypatch.setattr(SharedWriter,'compact',count)
    with pytest.raises(Cancelled):search(store,run,cancel=lambda:generated[0]>=1)
    row=store.rows('SELECT id,pattern FROM results WHERE run_id=?',(run,))[0]
    delete(store,scope,[run],[row['id']],[row['pattern']]);search(store,run)
    assert query(store,run)[1]==remaining and not store.rows('SELECT 1 FROM results WHERE id=?',(row['id'],))
    foreign_keys(store)


def test_old_writer_cannot_reinsert_deleted_result_and_new_append_is_indexed(store,snapshot):
    run,p=candidate(store,snapshot)
    with store.connect() as db:row=list(db.execute('SELECT * FROM results WHERE id=?',(p['id'],)).fetchone())
    delete(store,'results',[run],[p['id']])
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO results VALUES(?,?,?,?,?,?,?,?,?)',row)
        assert not db.execute('SELECT 1 FROM results WHERE id=?',(p['id'],)).fetchone()
        row[0]='new-append';row[-1]=encode(dict(p,id='new-append'));db.execute('INSERT INTO results VALUES(?,?,?,?,?,?,?,?,?)',row)
    assert sync_run_fares(store,run)['indexed']==1
    foreign_keys(store)
