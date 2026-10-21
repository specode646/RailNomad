"""Build public browser assets from a reviewed data bundle, never a user catalog."""
import hashlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from railnomad import __version__
from railnomad.domain.model import RULES_VERSION
from railnomad.domain.search import available_departure_dates
from railnomad.data.osm_mapping import station_fingerprint

source = ROOT / 'public-data'
record = json.loads((source / 'release.json').read_text(encoding='utf-8'))
assert record['distribution'] == 'public-offline' and record['evidence'] == []
with (source / 'snapshot.sqlite3').open('rb') as stream:
    assert hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
output = ROOT / 'web/dist/data'
output.mkdir(parents=True, exist_ok=True)
snapshot = dict(record['snapshot'], path=str((source / 'snapshot.sqlite3').resolve()))
with sqlite3.connect(Path(snapshot['path']).as_uri() + '?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    stations = [dict(row) for row in db.execute('SELECT id,name,city,lat,lon FROM stops ORDER BY name,id')]
reference = ROOT / 'railnomad/data/reference'
manifest = json.loads((reference / 'manifest.json').read_text(encoding='utf-8'))
import csv
table = reference / 'city-mapping.csv'
assert hashlib.sha256(table.read_bytes()).hexdigest() == manifest['csv_sha256']
with table.open(encoding='utf-8-sig', newline='') as stream:
    facts = {row['stop_id']: row['city'] for row in csv.DictReader(stream)}
mapping = {row['id']: facts[row['id']] for row in stations if row['id'] in facts and
           station_fingerprint(row) == manifest['station_fingerprints'].get(row['id'])}
dates = available_departure_dates(snapshot)
snapshot['path'] = '/data/snapshot.sqlite3'
shutil.copyfile(source / 'snapshot.sqlite3', output / 'snapshot.sqlite3')
metadata = dict(app_version=__version__, rules_version=RULES_VERSION,
                snapshot=dict(id=snapshot['id'], tag=snapshot['tag'], calendar=json.loads(snapshot['quality']).get('calendar', [])),
                departure_dates=dates, cities=sorted({mapping.get(row['id']) or row['city'] for row in stations if mapping.get(row['id']) or row['city']}),
                stations=[dict(id=row['id'], name=row['name']) for row in stations], mapping_count=len(mapping), distribution='public-offline')
sources = []
for path in (ROOT / 'railnomad').rglob('*.py'):
    relative = path.relative_to(ROOT)
    if 'ui' not in relative.parts and path.name not in ('__main__.py', 'worker.py', 'public_data.py'):
        sources.append(dict(path=relative.as_posix(), source=path.read_text(encoding='utf-8')))
for name, value in [('manifest.json', metadata), ('input.json', dict(snapshot=snapshot, mapping=mapping, evidence=[])), ('sources.json', sources)]:
    (output / name).write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
print(json.dumps(dict(public=True, stations=len(stations), mapping=len(mapping), private_evidence=0)))
