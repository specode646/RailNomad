"""Validated GTFS import into an isolated, immutable SQLite snapshot."""
import csv
import hashlib
import io
import json
import shutil
import sqlite3
import stat
import zipfile
from datetime import date, timedelta
from pathlib import Path, PurePosixPath
from uuid import uuid4

from ..storage import encode, now
from .release import check_cancel
from .timetable import repair_midnight_arrivals,flag_unusable_events

IMPORTER_VERSION = '3-segment-endpoints'
MAX_EXPANDED = 8 * 1024 ** 3
MAX_ENTRIES = 2000


def minutes(value):
    parts = value.split(':')
    if len(parts) != 3:
        raise ValueError(f'非法 GTFS 时间：{value}')
    h, m, s = map(int, parts)
    if h < 0 or not 0 <= m < 60 or not 0 <= s < 60:
        raise ValueError(f'非法 GTFS 时间：{value}')
    # Seconds are retained, never rounded into a passing boundary.
    return h * 3600 + m * 60 + s


def parse_date(value):
    return date.fromisoformat(f'{value[:4]}-{value[4:6]}-{value[6:8]}')


def inspect_zip(archive):
    entries = archive.infolist()
    if len(entries) > MAX_ENTRIES or sum(e.file_size for e in entries) > MAX_EXPANDED:
        raise ValueError('ZIP 超过资源保护：2000 个文件或解压后 8 GiB；未导入任何数据')
    files = {}
    for entry in entries:
        name = entry.filename.replace('\\', '/')
        path = PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts or ':' in name or stat.S_ISLNK(entry.external_attr >> 16):
            raise ValueError('ZIP 含不安全路径或符号链接')
        if entry.is_dir():
            continue
        if entry.file_size > max(entry.compress_size, 1) * 1000:
            raise ValueError('ZIP 解压比例超过 1000 倍资源保护')
        if path.name in files:
            raise ValueError(f'ZIP 存在重名文件：{path.name}')
        files[path.name] = entry
    required = {'agency.txt', 'stops.txt', 'routes.txt', 'trips.txt', 'stop_times.txt'}
    if required - files.keys():
        raise ValueError('GTFS 缺少文件：' + ', '.join(sorted(required - files.keys())))
    if not {'calendar.txt', 'calendar_dates.txt'} & files.keys():
        raise ValueError('缺少运行日历，不能证明排班可行性')
    if 'frequencies.txt' in files:
        raise ValueError('此版本暂不支持 frequencies.txt；拒绝将频率班次静默遗漏')
    return files


def import_gtfs(source, store, metadata=None, progress=lambda **kw: None, cancel=lambda: False):
    source = Path(source)
    digest = hashlib.sha256()
    with source.open('rb') as stream:
        while block := stream.read(1024 * 1024):
            check_cancel(cancel)
            digest.update(block)
    source_sha256 = digest.hexdigest()
    # Source bytes alone cannot identify two different interpreted timetables.
    identity = hashlib.sha256((source_sha256+':'+IMPORTER_VERSION).encode()).hexdigest()
    metadata = dict(metadata or {})
    if metadata.get('digest') and metadata['digest'] != 'sha256:' + source_sha256:
        raise ValueError('SHA-256 摘要不符')
    existing = store.rows('SELECT id FROM snapshots WHERE id=?', (identity,))
    if existing:
        from .mapping import inherit_mapping
        inherit_mapping(store,identity)
        from .reference_mapping import apply_reference
        apply_reference(store,identity)
        return identity
    metadata.update(sha256=source_sha256, snapshot_id=identity, imported_at=now(), importer_version=IMPORTER_VERSION,
                    bytes=source.stat().st_size,
                    authenticity='upstream_digest_matched' if metadata.get('digest') else 'local_hash_only')
    stage = store.home / 'snapshots' / ('.staging-' + uuid4().hex)
    stage.mkdir(parents=True)
    db = sqlite3.connect(stage / 'gtfs.sqlite3')
    db.execute('PRAGMA foreign_keys=ON')
    warnings = []
    counts = {}
    declared_services = set()
    invalid_trips = set()
    quarantined_records = []
    try:
        db.executescript('''
            CREATE TABLE stops(id TEXT PRIMARY KEY,name TEXT NOT NULL,lat REAL,lon REAL,city TEXT,city_source TEXT,parent TEXT);
            CREATE TABLE routes(id TEXT PRIMARY KEY,name TEXT,type INTEGER NOT NULL);
            CREATE TABLE trips(id TEXT PRIMARY KEY,route TEXT REFERENCES routes(id),service TEXT,name TEXT,block TEXT);
            CREATE TABLE calls(trip TEXT REFERENCES trips(id),seq INTEGER,stop TEXT REFERENCES stops(id),arr INTEGER,dep INTEGER,pickup INTEGER,dropoff INTEGER,exact INTEGER,PRIMARY KEY(trip,seq));
            CREATE TABLE days(service TEXT,day TEXT,PRIMARY KEY(service,day));
        ''')
        with zipfile.ZipFile(source) as archive:
            files = inspect_zip(archive)
            for filename,entry in files.items():
                if filename not in ('agency.txt','stops.txt','routes.txt','trips.txt','stop_times.txt','calendar.txt','calendar_dates.txt'):
                    with archive.open(entry) as extra:
                        while extra.read(1024*1024):
                            check_cancel(cancel)
                    progress(stage='ZIP 完整性校验 ' + filename, current=entry.file_size)

            def rows(name, required):
                if name not in files:
                    return
                with archive.open(files[name]) as raw:
                    reader = csv.DictReader(io.TextIOWrapper(raw, encoding='utf-8-sig', newline=''))
                    if not set(required) <= set(reader.fieldnames or []):
                        raise ValueError(f'{name} 缺少必需列：{required}')
                    count = 0
                    for row in reader:
                        check_cancel(cancel)
                        count += 1
                        if count % 10000 == 0:
                            progress(stage='导入 ' + name, current=count)
                        yield row
                    counts[name] = count
                    progress(stage='导入 ' + name, current=count)

            agencies = list(rows('agency.txt', ['agency_name', 'agency_url', 'agency_timezone']))
            zones = {r['agency_timezone'] for r in agencies}
            if zones != {'Asia/Shanghai'}:
                raise ValueError('当前中国铁路搜索器仅支持明确的 Asia/Shanghai 时区')
            for row in rows('stops.txt', ['stop_id', 'stop_name', 'stop_lat', 'stop_lon']):
                lat, lon = float(row['stop_lat']), float(row['stop_lon'])
                if not -90 <= lat <= 90 or not -180 <= lon <= 180:
                    raise ValueError('站点经纬度不合法')
                city = row.get('city') or row.get('stop_city') or None
                db.execute('INSERT INTO stops VALUES(?,?,?,?,?,?,?)',
                           (row['stop_id'], row['stop_name'], lat, lon, city,
                            'feed:city' if city else 'unknown', row.get('parent_station') or None))
            for row in rows('routes.txt', ['route_id', 'route_type']):
                db.execute('INSERT INTO routes VALUES(?,?,?)', (row['route_id'], row.get('route_short_name') or row.get('route_long_name'), int(row['route_type'])))
            for row in rows('trips.txt', ['route_id', 'service_id', 'trip_id']):
                db.execute('INSERT INTO trips VALUES(?,?,?,?,?)', (row['trip_id'], row['route_id'], row['service_id'], row.get('trip_short_name') or row['trip_id'], row.get('block_id')))
            interpolated = 0
            conditional = 0
            for row in rows('stop_times.txt', ['trip_id', 'arrival_time', 'departure_time', 'stop_id', 'stop_sequence']):
                if not row['arrival_time'] or not row['departure_time']:
                    raise ValueError('存在缺失时刻；尚未实现可审计的插值，不能静默丢站')
                arr, dep = minutes(row['arrival_time']), minutes(row['departure_time'])
                pickup, dropoff = int(row.get('pickup_type') or 0), int(row.get('drop_off_type') or 0)
                if pickup not in range(4) or dropoff not in range(4):
                    raise ValueError('上下车限制不合法')
                if dep < arr:
                    invalid_trips.add(row['trip_id'])
                    quarantined_records.append(dict(row, reason='departure_before_arrival'))
                exact = int(row.get('timepoint') or 1)
                if exact not in (0,1) or int(row['stop_sequence']) < 0:
                    raise ValueError('timepoint 须为 0/1，stop_sequence 须为非负整数')
                interpolated += exact == 0
                conditional += pickup in (2, 3) or dropoff in (2, 3)
                db.execute('INSERT INTO calls VALUES(?,?,?,?,?,?,?,?)',
                           (row['trip_id'], int(row['stop_sequence']), row['stop_id'], arr, dep, pickup, dropoff, exact))
            repaired_records=repair_midnight_arrivals(db,cancel)
            time_issues=flag_unusable_events(db)
            affected_trips=sorted({r['trip'] for r in time_issues})
            original_anomalies=quarantined_records
            quarantined_records=[]
            invalid_trips=set()
            if repaired_records:
                warnings.append(f'{len({r["trip"] for r in repaired_records})} 趟服务的 {len(repaired_records)} 条跨午夜到达日偏移已修复；原始时刻及修复依据保留，非官方核验')
            # Keep all calls for city/track sequence checks. Only ambiguous
            # boarding/alighting event values are unavailable for a segment.
            db.execute('CREATE TABLE quarantined_trips(trip TEXT PRIMARY KEY,reason TEXT NOT NULL)')
            if affected_trips:
                warnings.append(f'{len(affected_trips)} 趟服务仍有中间站时刻异常；保留其他可用区间，仅排除直接冲突的上下车时刻；异常区间须披露，覆盖不完整')
            for row in rows('calendar.txt', ['service_id', 'start_date', 'end_date', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']):
                declared_services.add(row['service_id'])
                start, end = parse_date(row['start_date']), parse_date(row['end_date'])
                if end < start or (end-start).days > 3660:
                    raise ValueError('日历范围无效或超过 10 年资源保护')
                flags = [int(row[k]) for k in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']]
                if any(v not in (0, 1) for v in flags):
                    raise ValueError('运行日历星期字段非法')
                for offset in range((end-start).days + 1):
                    day = start + timedelta(days=offset)
                    if flags[day.weekday()]:
                        db.execute('INSERT INTO days VALUES(?,?)', (row['service_id'], day.isoformat()))
            for row in rows('calendar_dates.txt', ['service_id', 'date', 'exception_type']):
                declared_services.add(row['service_id'])
                day = parse_date(row['date']).isoformat()
                if row['exception_type'] == '1':
                    db.execute('INSERT OR IGNORE INTO days VALUES(?,?)', (row['service_id'], day))
                elif row['exception_type'] == '2':
                    db.execute('DELETE FROM days WHERE service=? AND day=?', (row['service_id'], day))
                else:
                    raise ValueError('日历例外类型非法')
            missing_services = {r[0] for r in db.execute('SELECT DISTINCT service FROM trips')} - declared_services
            if missing_services:
                raise ValueError('服务引用了未声明日历：' + ', '.join(sorted(missing_services)))
            orphan = db.execute('SELECT COUNT(*) FROM trips WHERE service NOT IN(SELECT service FROM days)').fetchone()[0]
            if orphan:
                warnings.append(f'{orphan} 趟服务在快照中无实际运行日，不生成排班实例')
            if db.execute('SELECT 1 FROM stops WHERE parent IS NOT NULL AND parent NOT IN(SELECT id FROM stops) LIMIT 1').fetchone():
                raise ValueError('parent_station 引用不存在的站点')
        check_cancel(cancel)
        progress(stage='建立索引与质量报告', current=0)
        db.executescript('CREATE INDEX calls_stop ON calls(stop,dep); CREATE INDEX days_service ON days(service); CREATE INDEX trips_service ON trips(service);')
        quality = {'counts': counts, 'quarantined_trips': sorted(invalid_trips), 'quarantined_records': quarantined_records,
                   'original_time_anomalies':original_anomalies,'repaired_records':repaired_records,
                   'repaired_trips':sorted({r['trip'] for r in repaired_records}),
                   'timetable_policy':'segment_endpoints_v1','time_affected_trips':affected_trips,'time_issues':time_issues,
                   'unusable_time_events':len({(r['trip'],r['seq'],r['event']) for r in time_issues}),
                   'coverage_complete': not time_issues, 'warnings': warnings, 'interpolated_calls': interpolated,
                   'conditional_calls': conditional, 'unknown_cities': db.execute('SELECT COUNT(*) FROM stops WHERE city IS NULL').fetchone()[0],
                   'calendar': list(db.execute('SELECT MIN(day),MAX(day) FROM days').fetchone()),
                   'categories': dict(db.execute('SELECT type,COUNT(*) FROM routes GROUP BY type')),
                   'train_categories': dict(db.execute("SELECT CASE WHEN SUBSTR(name,1,1) GLOB '[0-9]' THEN 'numeric' ELSE SUBSTR(name,1,1) END,COUNT(*) FROM trips GROUP BY 1")),
                   'sleeper': 'unknown', 'fares': 'unknown', 'physical_track': 'unknown',
                   'resource_limits': {'expanded_bytes': MAX_EXPANDED, 'entries': MAX_ENTRIES}}
        if not quality['calendar'][0]:
            raise ValueError('快照没有任何实际运行日期')
        quality['warnings'].append('GTFS 不提供已核实卧铺、票价或物理铁路区段；候选保留待核验')
        previous = store.snapshots()
        same_source=[s for s in previous if json.loads(s['metadata']).get('sha256')==source_sha256]
        metadata['same_source_predecessors']=[s['id'] for s in same_source]
        if previous:
            old = json.loads(previous[0]['quality'])['counts']
            quality['scale_change'] = {k: counts[k] - old.get(k, 0) for k in counts}
        db.commit()
        db.close()
        # Keep original bytes for reproducibility. Never activate an unfinished import.
        shutil.copyfile(source, stage / 'source.zip')
        (stage / 'metadata.json').write_text(encode(metadata), encoding='utf-8')
        target = store.home / 'snapshots' / identity
        if target.exists():
            # A prior interrupted catalog commit can leave a fully written orphan.
            target = store.home / 'snapshots' / (identity + '-' + uuid4().hex[:8])
        check_cancel(cancel)
        stage.rename(target)
        with store.connect() as catalog:
            catalog.execute('INSERT INTO snapshots VALUES(?,?,?,?,?,?)',
                            (identity, metadata.get('tag', source.name), str(target / 'gtfs.sqlite3'), encode(metadata), encode(quality), now()))
            # Same bytes, unchanged departures and station IDs: preserve dated
            # quotes and explicit city provenance across importer revisions.
            for old in reversed(same_source):
                mapping=store.setting('city_mapping:'+old['id'])
                if mapping:
                    catalog.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping:'+identity,encode(mapping)))
                    provenance=store.setting('city_mapping_provenance:'+old['id'])
                    if provenance:
                        # Evidence IDs change below when copied into the new snapshot.
                        copied_sources=[]
                        for record in catalog.execute("SELECT id,kind,payload FROM evidence WHERE snapshot=? AND kind='city_membership'",(old['id'],)).fetchall():
                            if record['id'] in provenance.get('evidence_ids',[]):
                                copied_sources.append(hashlib.sha256(encode([identity,record['kind'],record['payload']]).encode()).hexdigest())
                        catalog.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',('city_mapping_provenance:'+identity,encode(dict(provenance,evidence_ids=copied_sources))))
                    else:
                        catalog.execute('DELETE FROM settings WHERE key=?',('city_mapping_provenance:'+identity,))
                for record in catalog.execute('SELECT kind,payload,created FROM evidence WHERE snapshot=?',(old['id'],)).fetchall():
                    key=hashlib.sha256(encode([identity,record['kind'],record['payload']]).encode()).hexdigest()
                    catalog.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',(key,identity,record['kind'],record['payload'],record['created']))
        from .mapping import inherit_mapping
        inherit_mapping(store,identity)
        return identity
    finally:
        db.close()
        if stage.exists():
            shutil.rmtree(stage)

