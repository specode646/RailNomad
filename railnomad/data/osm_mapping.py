"""Verified, name-only OSM geocoding packages; no geometry in the runtime."""
import csv
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from ..storage import encode, now
from .release import check_cancel

ATTRIBUTION = '© OpenStreetMap contributors · https://www.openstreetmap.org/copyright'
GUIDELINE = 'https://osmfoundation.org/wiki/Licence/Community_Guidelines/Geocoding_-_Guideline'


def station_fingerprint(stop):
    """An identity check without redistributing station coordinates in the package."""
    values = [stop['id'], stop['name'], stop['lat'], stop['lon']]
    return hashlib.sha256(encode(values).encode('utf-8')).hexdigest()


def read_package(path):
    path = Path(path)
    if path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError('OSM 归属清单超过 8 MiB')
    manifest = json.loads(path.read_text(encoding='utf-8'))
    if manifest.get('format') != 'railnomad-osm-names-v1' or manifest.get('reviewed') is not True:
        raise ValueError('需要已审查的 OSM 名称归属包；候选或未审查数据不能启用')
    if manifest.get('input_license') != 'ODbL-1.0' or manifest.get('guideline') != GUIDELINE:
        raise ValueError('OSM 归属包缺少许可或地理编码依据')
    if not manifest.get('coverage') or not manifest.get('inputs'):
        raise ValueError('OSM 归属包缺少覆盖范围或输入来源')
    for source in manifest['inputs']:
        if source.get('license') != 'ODbL-1.0' or len(source.get('sha256', '')) != 64 or not source.get('url'):
            raise ValueError('OSM 输入来源记录不完整')
    csv_path = path.parent / 'city-mapping.csv'
    if csv_path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('OSM 归属表超过 16 MiB')
    digest = hashlib.sha256(csv_path.read_bytes()).hexdigest()
    if digest != manifest.get('csv_sha256'):
        raise ValueError('OSM 归属表指纹不匹配')
    records = {}
    with csv_path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ['stop_id', 'city', 'source']:
            raise ValueError('OSM 名称表只能含 stop_id、city、source 三列')
        for row in reader:
            sid = row['stop_id']
            if not sid or sid in records or not row['city'].strip() or not row['source'].strip():
                raise ValueError('OSM 归属表含重复站号或空值')
            records[sid] = row
            if len(records) > 50000:
                raise ValueError('OSM 归属表超过 50,000 站')
    if set(records) != set(manifest.get('station_fingerprints', {})) or len(records) != manifest.get('rows'):
        raise ValueError('OSM 归属表与身份清单不一致')
    return manifest, records


def preview_package(store, snapshot, path, cancel=lambda: False):
    manifest, records = read_package(path)
    with closing(sqlite3.connect(Path(store.snapshot(snapshot)['path']).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        stops = [dict(row) for row in db.execute('SELECT id,name,lat,lon FROM stops')]
    matched = []
    for stop in stops:
        check_cancel(cancel)
        sid = stop['id']
        if sid in records and None not in (stop['lat'], stop['lon']) and station_fingerprint(stop) == manifest['station_fingerprints'][sid]:
            matched.append(records[sid])
    current = store.setting('city_mapping:' + snapshot, {})
    mapping = {row['stop_id']: row['city'] for row in matched}
    return dict(manifest=manifest, records=matched, mapping=mapping, stations=len(stops),
                unknown=len(stops) - len(mapping), removed=len(set(current) - set(mapping)),
                changed=sum(city != current[sid] for sid, city in mapping.items() if sid in current))


def install_package(store, snapshot, path, progress=lambda **kw: None, cancel=lambda: False):
    prepared = preview_package(store, snapshot, path, cancel)
    if not prepared['records']:
        raise ValueError('此包与运行图没有身份完全一致的车站，未修改归属')
    check_cancel(cancel)
    manifest = prepared['manifest']
    payload = encode(prepared['records'])
    identity = hashlib.sha256(encode([snapshot, 'osm_names', payload]).encode()).hexdigest()
    provenance = dict(provider='OpenStreetMap', attribution=ATTRIBUTION,
                      replacement=True,
                      input_license='ODbL-1.0', guideline=GUIDELINE,
                      coverage=manifest['coverage'], inputs=manifest['inputs'],
                      evidence_ids=[identity], rows=len(prepared['mapping']),
                      unknown=prepared['unknown'], installed_at=now())
    with store.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        check_cancel(cancel)
        # Replacement is deliberate: no legacy city can survive as a false OSM match.
        # Old evidence remains available; existing tasks already froze their mappings.
        db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',
                   (identity, snapshot, 'city_membership', payload, now()))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   ('city_mapping:' + snapshot, encode(prepared['mapping'])))
        db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)',
                   ('city_mapping_provenance:' + snapshot, encode(provenance)))
    progress(stage='OSM 城市归属已启用', current=len(prepared['mapping']))
    return dict(mapped=len(prepared['mapping']), unknown=prepared['unknown'],
                removed=prepared['removed'], changed=prepared['changed'])
