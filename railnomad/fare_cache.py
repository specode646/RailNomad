"""Quote change log and inverted route dependencies, independent of UI objects."""
import json
from .payloads import unpack
from .evidence import boarding_date

VERSION='indexed-fares-v1'


def install(db):
    columns=[row[1] for row in db.execute('PRAGMA table_info(fare_dependencies)')]
    if columns and 'result_key' not in columns:
        # An unpublished cache layout stored long IDs in every posting.
        # Only derived postings are discarded; tasks, quotes and budgets stay.
        db.execute('DROP TABLE fare_dependencies')
        db.execute('DELETE FROM fare_index_state')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS fare_keys(
            id INTEGER PRIMARY KEY,snapshot TEXT NOT NULL,trip TEXT NOT NULL,
            origin TEXT NOT NULL,destination TEXT NOT NULL,day TEXT NOT NULL,
            UNIQUE(snapshot,trip,origin,destination,day));
        CREATE TABLE IF NOT EXISTS fare_changes(
            seq INTEGER PRIMARY KEY AUTOINCREMENT,key_id INTEGER NOT NULL REFERENCES fare_keys(id));
        CREATE INDEX IF NOT EXISTS fare_changes_key ON fare_changes(key_id,seq);
        CREATE TABLE IF NOT EXISTS fare_revisions(snapshot TEXT PRIMARY KEY,seq INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS fare_dependency_runs(
            id INTEGER PRIMARY KEY,run_id TEXT UNIQUE NOT NULL REFERENCES runs(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS fare_dependency_results(
            id INTEGER PRIMARY KEY,result_id TEXT UNIQUE NOT NULL REFERENCES results(id) ON DELETE CASCADE);
        CREATE TABLE IF NOT EXISTS fare_dependencies(
            run_key INTEGER NOT NULL REFERENCES fare_dependency_runs(id) ON DELETE CASCADE,
            key_id INTEGER NOT NULL REFERENCES fare_keys(id),
            result_key INTEGER NOT NULL REFERENCES fare_dependency_results(id) ON DELETE CASCADE,
            PRIMARY KEY(run_key,key_id,result_key)) WITHOUT ROWID;
        CREATE INDEX IF NOT EXISTS fare_dependencies_result ON fare_dependencies(result_key,key_id);
        DROP TRIGGER IF EXISTS fare_result_delete;
        CREATE TABLE IF NOT EXISTS fare_index_state(run_id TEXT PRIMARY KEY,through_rowid INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS evidence_fare_lookup ON evidence(
            snapshot,json_extract(payload,'$.trip'),json_extract(payload,'$.from'),
            json_extract(payload,'$.to'),json_extract(payload,'$.date')) WHERE kind='fare';
        CREATE TRIGGER IF NOT EXISTS fare_quote_insert AFTER INSERT ON evidence WHEN NEW.kind='fare' BEGIN
            INSERT OR IGNORE INTO fare_keys(snapshot,trip,origin,destination,day)
            VALUES(NEW.snapshot,json_extract(NEW.payload,'$.trip'),json_extract(NEW.payload,'$.from'),json_extract(NEW.payload,'$.to'),json_extract(NEW.payload,'$.date'));
            INSERT INTO fare_changes(key_id) SELECT id FROM fare_keys WHERE snapshot=NEW.snapshot
            AND trip=json_extract(NEW.payload,'$.trip') AND origin=json_extract(NEW.payload,'$.from')
            AND destination=json_extract(NEW.payload,'$.to') AND day=json_extract(NEW.payload,'$.date');
        END;
        CREATE TRIGGER IF NOT EXISTS fare_quote_delete AFTER DELETE ON evidence WHEN OLD.kind='fare' BEGIN
            INSERT INTO fare_changes(key_id) SELECT id FROM fare_keys WHERE snapshot=OLD.snapshot
            AND trip=json_extract(OLD.payload,'$.trip') AND origin=json_extract(OLD.payload,'$.from')
            AND destination=json_extract(OLD.payload,'$.to') AND day=json_extract(OLD.payload,'$.date');
        END;
    ''')
    # Existing evidence is indexed exactly once. Both normal import and live
    # observations subsequently enter the same transactionally maintained log.
    if not db.execute("SELECT 1 FROM settings WHERE key='fare_cache_schema'").fetchone():
        db.execute('''INSERT OR IGNORE INTO fare_keys(snapshot,trip,origin,destination,day)
            SELECT snapshot,json_extract(payload,'$.trip'),json_extract(payload,'$.from'),
            json_extract(payload,'$.to'),json_extract(payload,'$.date') FROM evidence WHERE kind='fare' ''')
        db.execute('INSERT INTO fare_changes(key_id) SELECT id FROM fare_keys')
        db.execute("INSERT INTO settings VALUES('fare_cache_schema','1')")
    marker=db.execute("SELECT value FROM settings WHERE key='fare_revision_schema'").fetchone()
    if not marker or marker[0]!='2':
        if not db.in_transaction:db.execute('BEGIN IMMEDIATE')
        db.execute('DROP TRIGGER IF EXISTS fare_revision_insert')
        # The outer INSERT OR IGNORE used for deduplicating observations also
        # affects trigger conflict policies. Advance existing rows via UPDATE.
        db.execute('''CREATE TRIGGER fare_revision_insert AFTER INSERT ON fare_changes BEGIN
            UPDATE fare_revisions SET seq=NEW.seq WHERE snapshot=(SELECT snapshot FROM fare_keys WHERE id=NEW.key_id);
            INSERT OR IGNORE INTO fare_revisions SELECT snapshot,NEW.seq FROM fare_keys WHERE id=NEW.key_id;
        END''')
        db.execute('''INSERT OR REPLACE INTO fare_revisions
            SELECT k.snapshot,MAX(c.seq) FROM fare_changes c JOIN fare_keys k ON k.id=c.key_id GROUP BY k.snapshot''')
        db.execute("INSERT OR REPLACE INTO settings VALUES('fare_revision_schema','2')")


def payload_keys(payload):
    return {(leg['trip'],leg['from'],leg['to'],boarding_date(start,leg['dep']//86400))
            for start in payload['dates'] for leg in payload['legs']}


def index_results(db,run_id,cancel=lambda:False,progress=lambda **kw:None):
    """Index newly appended rows only; compact payloads already contain keys."""
    from .data.release import check_cancel
    state=db.execute('SELECT through_rowid FROM fare_index_state WHERE run_id=?',(run_id,)).fetchone()
    cursor=state[0] if state else 0
    snapshot=db.execute('SELECT snapshot FROM runs WHERE id=?',(run_id,)).fetchone()[0]
    db.execute('INSERT OR IGNORE INTO fare_dependency_runs(run_id) VALUES(?)',(run_id,))
    run_key=db.execute('SELECT id FROM fare_dependency_runs WHERE run_id=?',(run_id,)).fetchone()[0]
    known={};checked=0;upper=cursor
    while True:
        rows=db.execute('SELECT rowid,id,payload FROM results INDEXED BY results_run_stream WHERE run_id=? AND rowid>? ORDER BY rowid LIMIT 2000',(run_id,upper)).fetchall()
        if not rows:break
        pending=[]
        for row in rows:
            check_cancel(cancel)
            payload=json.loads(unpack(row['payload']))
            db.execute('INSERT OR IGNORE INTO fare_dependency_results(result_id) VALUES(?)',(row['id'],))
            result_key=db.execute('SELECT id FROM fare_dependency_results WHERE result_id=?',(row['id'],)).fetchone()[0]
            for key in payload_keys(payload):
                if key not in known:
                    values=(snapshot,)+key
                    db.execute('INSERT OR IGNORE INTO fare_keys(snapshot,trip,origin,destination,day) VALUES(?,?,?,?,?)',values)
                    known[key]=db.execute('SELECT id FROM fare_keys WHERE snapshot=? AND trip=? AND origin=? AND destination=? AND day=?',values).fetchone()[0]
                pending.append((run_key,known[key],result_key))
            upper=row['rowid'];checked+=1
        db.executemany('INSERT OR IGNORE INTO fare_dependencies VALUES(?,?,?)',pending)
        db.execute('INSERT OR REPLACE INTO fare_index_state VALUES(?,?)',(run_id,upper))
        progress(stage='首次建立票价关联索引',current=checked)
    return cursor,upper,checked


def revision(db,snapshot,run_id=None):
    row=db.execute('SELECT seq FROM fare_revisions WHERE snapshot=?',(snapshot,)).fetchone()
    seq=row[0] if row else 0
    last=db.execute('SELECT COALESCE(MAX(rowid),0) FROM results INDEXED BY results_run_stream WHERE run_id=?',(run_id,)).fetchone()[0] if run_id else 0
    return [VERSION,seq,last]


def affected_results(db,run_id,after,through,old_rowid):
    row=db.execute('SELECT id FROM fare_dependency_runs WHERE run_id=?',(run_id,)).fetchone()
    if not row:return set()
    run_key=row[0]
    changed=db.execute('''SELECT DISTINCT d.result_key FROM fare_changes c
        CROSS JOIN fare_dependencies d
        WHERE c.seq>? AND c.seq<=? AND d.key_id=c.key_id AND d.run_key=?''',(after,through,run_key))
    result={row[0] for row in changed}
    if old_rowid is not None:
        # New results also need old quotes. This traverses only newly appended
        # results and their postings, never the previously indexed catalog.
        rows=db.execute('''SELECT DISTINCT m.id FROM results r INDEXED BY results_run_stream
            CROSS JOIN fare_dependency_results m CROSS JOIN fare_dependencies d
            WHERE r.run_id=? AND r.rowid>? AND m.result_id=r.id AND d.result_key=m.id AND EXISTS(
                SELECT 1 FROM fare_changes c WHERE c.key_id=d.key_id AND c.seq<=?)''',(run_id,old_rowid,through))
        result.update(row[0] for row in rows)
    return result


def targeted_index(db,snapshot,keys,cache=None):
    cache={} if cache is None else cache
    result={}
    for key in keys:
        if key not in cache:
            rows=db.execute('''SELECT payload FROM evidence WHERE kind='fare' AND snapshot=?
                AND json_extract(payload,'$.trip')=? AND json_extract(payload,'$.from')=?
                AND json_extract(payload,'$.to')=? AND json_extract(payload,'$.date')=?''',(snapshot,)+key)
            cache[key]=[json.loads(row[0]) for row in rows]
        if cache[key]:result[('fare',)+key]=cache[key]
    return result
