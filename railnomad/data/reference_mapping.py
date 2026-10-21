"""Reuse the shipped name-only facts for an otherwise unmapped snapshot."""
import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..storage import encode, now
from .osm_mapping import station_fingerprint


def apply_reference(store, snapshot, folder=None):
    if store.setting('city_mapping:'+snapshot, {}):
        return 0  # Existing user, inherited and OSM mappings take precedence.
    folder = Path(folder) if folder else Path(__file__).parent/'reference'
    manifest_path = folder/'manifest.json'
    if not manifest_path.is_file():
        return 0
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    table = folder/'city-mapping.csv'
    if hashlib.sha256(table.read_bytes()).hexdigest() != manifest['csv_sha256']:
        raise ValueError('随包站城表指纹不符，请重新取得完整程序包')
    with table.open(encoding='utf-8-sig', newline='') as stream:
        reference = {row['stop_id']:row for row in csv.DictReader(stream)}
    with closing(sqlite3.connect(Path(store.snapshot(snapshot)['path']).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        db.row_factory=sqlite3.Row
        records=[reference[row['id']] for row in db.execute('SELECT id,name,lat,lon FROM stops')
                 if row['id'] in reference and station_fingerprint(row)==manifest['station_fingerprints'].get(row['id'])]
    if not records:
        return 0
    identity=hashlib.sha256(encode([snapshot,'reference_city_facts',records]).encode()).hexdigest()
    provenance=dict(provider='站城事实表',evidence_ids=[identity],rows=len(records),
                    coverage=manifest['coverage'],attribution=manifest['attribution'],
                    license='Facts; upstream publisher notices retained separately',reference_sha256=manifest['csv_sha256'])
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        current=db.execute('SELECT value FROM settings WHERE key=?',('city_mapping:'+snapshot,)).fetchone()
        if current and json.loads(current[0]):return 0
        db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(identity,snapshot,'city_membership',encode(records),now()))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping:'+snapshot,encode({row['stop_id']:row['city'] for row in records})))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping_provenance:'+snapshot,encode(provenance)))
    return len(records)
