"""Application catalog. Snapshot databases are immutable after import."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from collections import OrderedDict
from copy import deepcopy
from .payloads import unpack


def now():
    return datetime.now(timezone.utc).isoformat()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class Store:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / "catalog.sqlite3"
        self.fragment_cache=OrderedDict()
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("数据库版本高于此程序，请使用较新版本打开")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS snapshots(
                    id TEXT PRIMARY KEY,tag TEXT NOT NULL,path TEXT NOT NULL,
                    metadata TEXT NOT NULL,quality TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs(
                    id TEXT PRIMARY KEY,snapshot TEXT NOT NULL REFERENCES snapshots(id),
                    request TEXT NOT NULL,status TEXT NOT NULL,checkpoint TEXT NOT NULL DEFAULT '{}',
                    audit TEXT NOT NULL DEFAULT '{}',created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS results(
                    id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id),
                    pattern TEXT NOT NULL,nights INTEGER NOT NULL,price REAL,
                    changes INTEGER NOT NULL,short_hsr INTEGER NOT NULL,
                    status TEXT NOT NULL,payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS results_run ON results(run_id,pattern,nights);
                CREATE INDEX IF NOT EXISTS results_run_stream ON results(run_id);
                CREATE INDEX IF NOT EXISTS results_run_page ON results(run_id,changes,id,nights,price,status,pattern,short_hsr);
                CREATE INDEX IF NOT EXISTS results_run_browser ON results(run_id,pattern,changes,id,nights,price,status,short_hsr);
                DROP INDEX IF EXISTS results_run_changes;
                DROP INDEX IF EXISTS results_run_pattern_changes;
                CREATE TABLE IF NOT EXISTS shared_parts(
                    run_id TEXT NOT NULL REFERENCES runs(id),part INTEGER NOT NULL,
                    kind TEXT NOT NULL,payload BLOB NOT NULL,
                    PRIMARY KEY(run_id,part));
                CREATE TABLE IF NOT EXISTS favorites(
                    result_id TEXT PRIMARY KEY REFERENCES results(id),created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS evidence(
                    id TEXT PRIMARY KEY,snapshot TEXT NOT NULL,kind TEXT NOT NULL,
                    payload TEXT NOT NULL,created TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exports(
                    id INTEGER PRIMARY KEY,run_id TEXT,scope TEXT,path TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS fare_assessments(
                    result_id TEXT PRIMARY KEY REFERENCES results(id),
                    payload TEXT NOT NULL,low REAL,high REAL,updated TEXT NOT NULL);
                PRAGMA user_version=2;
            """)
            from .fare_cache import install
            install(db)
            from .deletion import install as install_deletion
            install_deletion(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.create_function('rn_payload',1,unpack,deterministic=True)
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def rows(self, sql, args=()):
        with self.connect() as db:
            return [self.expand_row(r,db) for r in db.execute(sql, args)]

    def expand_row(self, row, db):
        result=dict(row)
        payload=result.get('payload')
        if isinstance(payload,bytes) or isinstance(payload,str) and '"__railnomad_shared"' in payload:
            result['payload']=encode(self.expand_payload(payload,db))
        return result

    def expand_payload(self, payload, db=None):
        if db is None:
            with self.connect() as connection:
                return self.expand_payload(payload,connection)
        value=json.loads(unpack(payload))
        if not isinstance(value,dict) or value.get('__railnomad_shared')!=1:
            return value
        return self.expand_shared({k:v for k,v in value.items() if k!='__railnomad_shared'},value['run_id'],db)

    def missing_keys(self,payload,db):
        value=json.loads(unpack(payload))
        keys=value.get('missing_evidence_keys',[])
        return self.expand_shared(keys,value['run_id'],db) if value.get('__railnomad_shared')==1 else keys

    def expand_shared(self,item,run,db):
        if isinstance(item,list):
            return [self.expand_shared(v,run,db) for v in item]
        if not isinstance(item,dict):
            return item
        if '$shared' not in item:
            return {k:self.expand_shared(v,run,db) for k,v in item.items()}
        part=item['$shared']
        if type(part) is not int or part<1:
            raise ValueError('结果片段引用无效')
        key=run,part
        if key not in self.fragment_cache:
            row=db.execute('SELECT kind,payload FROM shared_parts WHERE run_id=? AND part=?',key).fetchone()
            if row is None:
                raise ValueError('结果引用的公共片段缺失')
            self.fragment_cache[key]=row['kind'],json.loads(unpack(row['payload']))
            if len(self.fragment_cache)>4096:
                self.fragment_cache.popitem(last=False)
        kind,content=self.fragment_cache[key]
        self.fragment_cache.move_to_end(key)
        if kind not in ('leg','leg_evidence','missing'):
            raise ValueError('未知结果片段类型')
        result=deepcopy(content)
        if kind=='leg_evidence':
            result['leg']=item['leg']
        return result

    def setting(self, key, default=None):
        rows = self.rows("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", (key, encode(value)))

    def snapshots(self):
        return self.rows("SELECT * FROM snapshots ORDER BY created DESC")

    def snapshot(self, identity=None):
        identity = identity or self.setting("active_snapshot")
        rows = self.rows("SELECT * FROM snapshots WHERE id=?", (identity,))
        if not rows:
            raise ValueError("尚未启用数据，请先下载或导入 GTFS")
        return rows[0]

    def activate(self, identity):
        snap = self.snapshot(identity)
        if not Path(snap["path"]).is_file():
            raise ValueError("快照文件丢失，请重新导入")
        from .data.mapping import inherit_mapping
        inherit_mapping(self,identity)
        from .data.reference_mapping import apply_reference
        apply_reference(self,identity)
        self.set_setting("active_snapshot", identity)
