"""Atomic catalog deletion with persistent exclusions for resumable searches."""
import json,sqlite3
from .storage import encode,now
from .data.release import Cancelled,check_cancel


def install(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS deleted_results(
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            result_id TEXT NOT NULL,deleted TEXT NOT NULL,PRIMARY KEY(run_id,result_id)) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS deleted_patterns(
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            pattern TEXT NOT NULL,deleted TEXT NOT NULL,PRIMARY KEY(run_id,pattern)) WITHOUT ROWID;
        CREATE TRIGGER IF NOT EXISTS prevent_deleted_result BEFORE INSERT ON results
        WHEN EXISTS(SELECT 1 FROM deleted_results WHERE run_id=NEW.run_id AND result_id=NEW.id)
          OR EXISTS(SELECT 1 FROM deleted_patterns WHERE run_id=NEW.run_id AND pattern=NEW.pattern)
        BEGIN SELECT RAISE(IGNORE); END;
    ''')


def _selection(db,scope,run_ids,result_ids=(),patterns=()):
    if scope not in ('tasks','patterns','results'):raise ValueError('删除范围无效')
    db.execute('CREATE TEMP TABLE selected_runs(id TEXT PRIMARY KEY)')
    db.executemany('INSERT OR IGNORE INTO selected_runs VALUES(?)',[(value,) for value in run_ids])
    db.execute('DELETE FROM selected_runs WHERE id NOT IN(SELECT id FROM runs)')
    db.execute('CREATE TEMP TABLE selected_results(id TEXT PRIMARY KEY)')
    if scope=='tasks':
        db.execute('INSERT INTO selected_results SELECT id FROM results WHERE run_id IN(SELECT id FROM selected_runs)')
    else:
        db.execute('CREATE TEMP TABLE selected_values(value TEXT PRIMARY KEY)')
        values=patterns if scope=='patterns' else result_ids
        db.executemany('INSERT OR IGNORE INTO selected_values VALUES(?)',[(value,) for value in values])
        column='pattern' if scope=='patterns' else 'id'
        db.execute('INSERT INTO selected_results SELECT id FROM results WHERE run_id IN(SELECT id FROM selected_runs) AND '+column+' IN(SELECT value FROM selected_values)')
    return dict(scope=scope,run_ids=[row[0] for row in db.execute('SELECT id FROM selected_runs')],
                deleted_results=db.execute('SELECT COUNT(*) FROM selected_results').fetchone()[0],
                deleted_runs=db.execute('SELECT COUNT(*) FROM selected_runs').fetchone()[0] if scope=='tasks' else 0,
                deleted_groups=db.execute('SELECT COUNT(*) FROM(SELECT DISTINCT run_id,pattern FROM results WHERE id IN(SELECT id FROM selected_results))').fetchone()[0] if scope=='patterns' else 0)


def preview(store,scope,run_ids,result_ids=(),patterns=()):
    with store.connect() as db:return _selection(db,scope,run_ids,result_ids,patterns)


def _update_audit(db,run_id):
    row=db.execute('SELECT audit FROM runs WHERE id=?',(run_id,)).fetchone()
    if not row:return
    audit=json.loads(row[0]);retained,groups=db.execute('SELECT COUNT(*),COUNT(DISTINCT pattern) FROM results WHERE run_id=?',(run_id,)).fetchone()
    audit.setdefault('before_user_deletions',{key:audit[key] for key in ('complete_candidates','city_combinations','result_states','missing_evidence_keys') if key in audit})
    audit.update(complete_candidates=retained,city_combinations=groups,
                 result_states=dict(db.execute('SELECT status,COUNT(*) FROM results WHERE run_id=? GROUP BY status',(run_id,))),
                 user_deleted_candidates=db.execute('SELECT COUNT(*) FROM deleted_results WHERE run_id=?',(run_id,)).fetchone()[0],
                 user_deleted_patterns=db.execute('SELECT COUNT(*) FROM deleted_patterns WHERE run_id=?',(run_id,)).fetchone()[0])
    audit.pop('missing_evidence_keys',None)
    audit['catalog_summary']='候选、组合与状态计数为当前保留目录；访问与排除统计及 before_user_deletions 为历史检索记录'
    db.execute('UPDATE runs SET audit=? WHERE id=?',(encode(audit),run_id))
    # Rowids may be reused after deleting the tail of a task. Keep the append
    # frontier and the fare signature aligned without replaying existing quotes.
    tail=db.execute('SELECT COALESCE(MAX(rowid),0) FROM results WHERE run_id=?',(run_id,)).fetchone()[0]
    db.execute('UPDATE fare_index_state SET through_rowid=MIN(through_rowid,?) WHERE run_id=?',(tail,run_id))
    key='fare_sync:'+run_id;row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
    if row:
        signature=json.loads(row[0])
        if isinstance(signature,list) and len(signature)==3 and signature[0]=='indexed-fares-v1':
            signature[2]=tail;db.execute('UPDATE settings SET value=? WHERE key=?',(encode(signature),key))


def delete(store,scope,run_ids,result_ids=(),patterns=(),progress=lambda **kw:None,cancel=lambda:False):
    check_cancel(cancel)
    try:
        with store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.set_progress_handler(lambda:int(bool(cancel())),50000)
            selection=_selection(db,scope,run_ids,result_ids,patterns)
            if not selection['deleted_results'] and not selection['deleted_runs']:return selection
            progress(stage='删除所选内容',current=0,total=selection['deleted_results'])
            if scope!='tasks':
                db.execute('INSERT OR IGNORE INTO deleted_results SELECT run_id,id,? FROM results WHERE id IN(SELECT id FROM selected_results)',(now(),))
                if scope=='patterns':db.execute('INSERT OR IGNORE INTO deleted_patterns SELECT DISTINCT run_id,pattern,? FROM results WHERE id IN(SELECT id FROM selected_results)',(now(),))
            db.execute('DELETE FROM favorites WHERE result_id IN(SELECT id FROM selected_results)')
            db.execute('DELETE FROM fare_assessments WHERE result_id IN(SELECT id FROM selected_results)')
            db.execute('DELETE FROM results WHERE id IN(SELECT id FROM selected_results)')
            if scope=='tasks':
                db.execute('DELETE FROM shared_parts WHERE run_id IN(SELECT id FROM selected_runs)')
                db.execute('DELETE FROM fare_index_state WHERE run_id IN(SELECT id FROM selected_runs)')
                db.execute("DELETE FROM settings WHERE key IN(SELECT 'fare_sync:'||id FROM selected_runs)")
                db.execute('DELETE FROM exports WHERE run_id IN(SELECT id FROM selected_runs)')
                db.execute('DELETE FROM runs WHERE id IN(SELECT id FROM selected_runs)')
            else:
                for run in selection['run_ids']:_update_audit(db,run)
            check_cancel(cancel)
            db.execute("INSERT INTO settings VALUES('catalog_revision','1') ON CONFLICT(key) DO UPDATE SET value=CAST(CAST(value AS INTEGER)+1 AS TEXT)")
            db.set_progress_handler(None,0)
        for key in list(store.fragment_cache):
            if scope=='tasks' and key[0] in selection['run_ids']:store.fragment_cache.pop(key,None)
        return selection
    except sqlite3.OperationalError as exc:
        if 'interrupted' in str(exc).lower():raise Cancelled('删除已取消，原目录保持不变') from exc
        raise
