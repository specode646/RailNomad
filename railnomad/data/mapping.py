"""Explicit city mappings with atomic provenance and settings updates."""
import csv,sqlite3,json,hashlib
from contextlib import closing
from pathlib import Path
from uuid import uuid4
from ..storage import encode,now
from .release import check_cancel


def mapping_sources(db,snapshot):
    """Only current mapping evidence enters new tasks; archives remain untouched."""
    row=db.execute('SELECT value FROM settings WHERE key=?',('city_mapping_provenance:'+snapshot,)).fetchone()
    provenance=json.loads(row[0]) if row else {}
    records=[dict(r) for r in db.execute("SELECT id,payload,created FROM evidence WHERE snapshot=? AND kind='city_membership' ORDER BY created,id",(snapshot,))]
    if 'evidence_ids' in provenance:
        records=[r for r in records if r['id'] in provenance['evidence_ids']]
    return records


def inherit_mapping(store,snapshot,cancel=lambda:False):
    """Carry proven station identities forward; never copy dated fare evidence."""
    target=store.snapshot(snapshot)
    def stations(snap):
        with closing(sqlite3.connect(Path(snap['path']).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            return {r[0]:tuple(r[1:]) for r in db.execute('SELECT id,name,lat,lon FROM stops')}
    current=store.setting('city_mapping:'+snapshot,{})
    if store.setting('city_mapping_provenance:'+snapshot,{}).get('replacement'):
        return 0
    predecessor=None;previous={}
    for snap in store.snapshots():
        if snap['id']==snapshot or snap['created']>target['created']:continue
        mappings=store.setting('city_mapping:'+snap['id'],{})
        if mappings and Path(snap['path']).is_file():
            predecessor=snap;previous=mappings;break
    if predecessor is None:return 0
    check_cancel(cancel)
    old=stations(predecessor);new=stations(target)
    eligible={sid:city for sid,city in previous.items() if sid not in current and sid in new and sid in old
              and new[sid]==old[sid] and all(v is not None for v in new[sid])}
    if not eligible:return 0
    sources={}
    with store.connect() as db:source_records=mapping_sources(db,predecessor['id'])
    for record in source_records:
        payload=json.loads(record['payload'])
        if isinstance(payload,list):
            for row in payload:
                if isinstance(row,dict) and row.get('stop_id') in eligible and row.get('city')==eligible[row['stop_id']]:sources[row['stop_id']]=row
    records=[]
    for sid,city in eligible.items():
        origin=sources.get(sid)
        if not origin or not origin.get('source'):continue
        records.append(dict(origin,stop_id=sid,city=city,inherited_from=predecessor['id'],identity_check='exact stop_id/name/latitude/longitude'))
    check_cancel(cancel)
    if not records:return 0
    with store.connect() as db:
        # Preserve a mapping imported concurrently while identities were checked.
        row=db.execute('SELECT value FROM settings WHERE key=?',('city_mapping:'+snapshot,)).fetchone()
        current=json.loads(row[0]) if row else {}
        records=[row for row in records if row['stop_id'] not in current]
        if not records:return 0
        current.update({row['stop_id']:row['city'] for row in records})
        payload=encode(records);key=hashlib.sha256(encode([snapshot,'inherited_city_membership',payload]).encode()).hexdigest()
        db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(key,snapshot,'city_membership',payload,now()))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping:'+snapshot,encode(current)))
        inherited_provenance=store.setting('city_mapping_provenance:'+predecessor['id'],{})
        if inherited_provenance and not current.keys()-eligible.keys():
            inherited_provenance=dict(inherited_provenance,evidence_ids=[key],rows=len(current),unknown=len(new)-len(current),inherited_from=predecessor['id'])
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping_provenance:'+snapshot,encode(inherited_provenance)))
    return len(records)


def import_mapping(store,snapshot,path,progress=lambda **kw:None,cancel=lambda:False):
    snap=store.snapshot(snapshot)
    with closing(sqlite3.connect(snap['path'])) as db:
        ids={r[0] for r in db.execute('SELECT id FROM stops')}
    mappings=store.setting('city_mapping:'+snapshot,{})
    records=[]
    with open(path,encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if not {'stop_id','city','source'} <= set(reader.fieldnames or []):
            raise ValueError('城市映射需要 stop_id,city,source 三列')
        for index,row in enumerate(reader):
            check_cancel(cancel)
            if row['stop_id'] not in ids or not row['city'].strip() or not row['source'].strip():
                raise ValueError('城市映射含未知站点、空城市或缺来源记录')
            mappings[row['stop_id']]=row['city'].strip();records.append(row)
            if index%1000==0:progress(stage='导入城市映射',current=index)
    check_cancel(cancel)
    with store.connect() as db:
        identity=uuid4().hex
        db.execute('INSERT INTO evidence VALUES(?,?,?,?,?)',(identity,snapshot,'city_membership',encode(records),now()))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping:'+snapshot,encode(mappings)))
        provenance=store.setting('city_mapping_provenance:'+snapshot,{})
        if provenance:
            provenance=dict(provenance,provider='手动导入 / 混合来源',evidence_ids=provenance['evidence_ids']+[identity],rows=len(mappings))
            db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping_provenance:'+snapshot,encode(provenance)))
    return len(records)
