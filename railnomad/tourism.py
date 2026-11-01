"""Locally supplied tourism values and one-time recommendation indexing."""
import csv
import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from .data.release import check_cancel
from .payloads import unpack

DATA = Path(__file__).parent / 'data' / 'tourism-scores.csv'
TRANSPORT = Path(__file__).parent / 'data' / 'transport-scores.csv'
VERSION = 'tourism-recommendation-v4'


def available():
    return DATA.is_file() and TRANSPORT.is_file()


def values():
    raw = DATA.read_bytes()
    with DATA.open(encoding='utf-8-sig', newline='') as f:
        records = list(csv.DictReader(f))
    result = {}
    for row in records:
        if not row['应用城市'] or not row['价值点'] or row['应用城市'] in result:
            raise ValueError('旅游资源评分表存在缺失或重复城市')
        result[row['应用城市']] = float(row['价值点'])
    transport_raw = TRANSPORT.read_bytes()
    traffic = {}
    with TRANSPORT.open(encoding='utf-8-sig', newline='') as f:
        seen = set()
        for row in csv.DictReader(f):
            city = row['应用城市']
            if not city or city in seen:
                raise ValueError('城市交通评分表存在缺失或重复城市')
            seen.add(city)
            if row['交通代理值']:
                val = float(row['交通代理值'])
                if not 0 <= val <= 1:raise ValueError('城市交通评分超出范围')
                traffic[city] = val
    return result, traffic, VERSION + ':' + hashlib.sha256(raw + b'\0' + transport_raw).hexdigest()


def install(db):
    db.execute('''CREATE TABLE IF NOT EXISTS tourism_rank (
        run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
        result_id TEXT NOT NULL PRIMARY KEY REFERENCES results(id) ON DELETE CASCADE,
        score REAL NOT NULL)''')
    db.execute('CREATE INDEX IF NOT EXISTS tourism_rank_order ON tourism_rank(run_id,score DESC,result_id)')
    db.execute('''CREATE TABLE IF NOT EXISTS tourism_rank_state (
        run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
        version TEXT NOT NULL, result_count INTEGER NOT NULL, last_rowid INTEGER NOT NULL)''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS tourism_rank_invalidate_insert AFTER INSERT ON results
        BEGIN DELETE FROM tourism_rank_state WHERE run_id=NEW.run_id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS tourism_rank_invalidate_update AFTER UPDATE OF payload,pattern,changes ON results
        BEGIN DELETE FROM tourism_rank_state WHERE run_id=NEW.run_id; END''')
    db.execute('''CREATE TRIGGER IF NOT EXISTS tourism_rank_invalidate_delete AFTER DELETE ON results
        BEGIN DELETE FROM tourism_rank_state WHERE run_id=OLD.run_id; END''')
    for table in ('favorites', 'fare_assessments'):
        for action in ('INSERT', 'UPDATE', 'DELETE'):
            db.execute(f'''CREATE TRIGGER IF NOT EXISTS tourism_filter_{table}_{action.lower()} AFTER {action} ON {table}
                BEGIN INSERT INTO settings VALUES('tourism_filter_revision','1')
                ON CONFLICT(key) DO UPDATE SET value=CAST(value AS INTEGER)+1; END''')


def daylight_overlap(start, end, day_start, day_end):
    total = 0
    for day in range(start // 86400, end // 86400 + 1):
        total += max(0, min(end, day * 86400 + day_end * 60) - max(start, day * 86400 + day_start * 60))
    return total


def score(payload, request, city_values, changes, traffic=None, station_cities=None):
    traffic = traffic or {}
    station_cities = station_cities or {}
    legs = payload.get('legs', [])
    start, end = request.get('daylight_start', 420), request.get('daylight_end', 1200)
    if not (isinstance(start, int) and isinstance(end, int) and 0 <= start < end <= 1440):
        start, end = 420, 1200
    endpoint_cities = {station_cities.get(legs[0].get('from')), station_cities.get(legs[-1].get('to'))} if legs else set()
    endpoint_cities.discard(None)
    stops = {}
    for i, (a, b) in enumerate(zip(legs, legs[1:])):
        if b['dep'] < a['arr']:continue
        left, right = station_cities.get(a.get('to')), station_cities.get(b.get('from'))
        city = left if left and left == right else None
        if city in endpoint_cities:continue
        key = city if city else ('unknown', i)
        stops[key] = stops.get(key, 0) + daylight_overlap(a['arr'], b['dep'], start, end) / 3600
    exposure = weighted_traffic = total_hours = 0.0
    for city, hours in stops.items():
        hours = min(hours, 8)
        value = min(1.0, math.log1p(city_values[city]) / math.log1p(1200)) if city in city_values else .5
        exposure += value * hours
        weighted_traffic += traffic.get(city, .5) * hours
        total_hours += hours
    experience = 1 - math.exp(-exposure / 8)
    local_transport = weighted_traffic / total_hours if total_hours else 0
    comfort = max(0, payload.get('comfort_minutes', payload.get('relaxation_minutes', 0)) or 0)
    transfers = max(0, len(legs) - 1)
    convenience = 1 / (1 + .35*transfers + .75*max(0, changes))
    comfort_score = 20 * max(0, 1 - comfort / 240)
    return round(55*experience + 15*local_transport + comfort_score + 10*convenience, 6)


def ready(store, run_id):
    _, _, version = values()
    with store.connect() as db:
        state = db.execute('SELECT version,result_count,last_rowid FROM tourism_rank_state WHERE run_id=?', (run_id,)).fetchone()
        return bool(state and state[0] == version)


def build(store, run_id, progress=lambda **kw: None, cancel=lambda: False):
    city_values, traffic, version = values()
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        request_row = db.execute('SELECT runs.request,runs.checkpoint,snapshots.path FROM runs JOIN snapshots ON snapshots.id=runs.snapshot WHERE runs.id=?', (run_id,)).fetchone()
        if not request_row:
            raise ValueError('检索任务不存在')
        request = json.loads(request_row[0])
        checkpoint = json.loads(request_row[1])
        station_cities = {}
        snapshot_path = Path(request_row[2])
        if snapshot_path.is_file():
            with sqlite3.connect(snapshot_path.resolve().as_uri()+'?mode=ro', uri=True) as snapshot_db:
                station_cities = {sid:city for sid,city in snapshot_db.execute('SELECT id,city FROM stops') if city}
        station_cities.update(checkpoint.get('mappings') or {})
        count, tail = db.execute('SELECT COUNT(*),COALESCE(MAX(rowid),0) FROM results WHERE run_id=?', (run_id,)).fetchone()
        state = db.execute('SELECT version,result_count,last_rowid FROM tourism_rank_state WHERE run_id=?', (run_id,)).fetchone()
        if state and tuple(state) == (version, count, tail):
            return {'indexed': 0, 'total': count, 'unchanged': True}
        db.execute('DELETE FROM tourism_rank WHERE run_id=?', (run_id,))
        batch = []
        for number, row in enumerate(db.execute('SELECT id,payload,changes FROM results WHERE run_id=?', (run_id,)), 1):
            if number % 500 == 0:
                check_cancel(cancel)
                progress(stage='计算综合排序', current=number, total=count)
            payload = json.loads(unpack(row['payload']))
            batch.append((run_id, row['id'], score(payload, request, city_values, row['changes'], traffic, station_cities)))
            if len(batch) == 1000:
                db.executemany('INSERT INTO tourism_rank VALUES(?,?,?)', batch)
                batch.clear()
        if batch:
            db.executemany('INSERT INTO tourism_rank VALUES(?,?,?)', batch)
        check_cancel(cancel)
        db.execute('INSERT OR REPLACE INTO tourism_rank_state VALUES(?,?,?,?)', (run_id, version, count, tail))
        db.execute("INSERT OR REPLACE INTO settings VALUES(?,?)", ('tourism_generation:'+run_id, str(time.time_ns())))
    return {'indexed': count, 'total': count}
