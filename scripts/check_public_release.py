"""Fail before publishing if private files or automatic upstream lookup leaks in."""
import argparse
import ast
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DENIED = ('queryTicketPrice', 'queryByTrainNo', 'urllib.request.urlopen', 'Mozilla/5.0 RailNomad')


def check_source():
    config = json.loads((ROOT / 'PUBLIC-RELEASE.json').read_text(encoding='utf-8'))
    assert config['edition'] == 'public-offline'
    for path in (ROOT / 'railnomad').rglob('*.py'):
        text = path.read_text(encoding='utf-8')
        ast.parse(text)
        if path.name == 'fares.py':
            assert not any(token in text for token in DENIED)
    assert not (ROOT / 'data').exists(), 'Public repository must not include local data/'
    assert not (ROOT / 'REPORT.md').exists(), 'Private acceptance report is outside public scope'
    assert not (ROOT / 'web/dist/data/input.json').exists() or json.loads((ROOT / 'web/dist/data/input.json').read_text(encoding='utf-8'))['evidence'] == []
    record = json.loads((ROOT / 'public-data/release.json').read_text(encoding='utf-8'))
    assert record['evidence'] == []
    with (ROOT / 'public-data/snapshot.sqlite3').open('rb') as stream:
        assert hashlib.file_digest(stream, 'sha256').hexdigest() == record['sha256']
    with sqlite3.connect((ROOT / 'public-data/snapshot.sqlite3').as_uri() + '?mode=ro', uri=True) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not tables & {'runs', 'results', 'evidence', 'fare_assessments', 'favorites', 'settings'}
        assert db.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
    text = (ROOT / 'web/providers.js').read_text(encoding='utf-8')
    assert '/api/v1' not in text and 'fetch(' not in text


def check_web():
    folder = ROOT / 'web/dist'
    config = json.loads((folder / 'data/input.json').read_text(encoding='utf-8'))
    assert config['evidence'] == []
    assert json.loads((folder / 'data/manifest.json').read_text(encoding='utf-8'))['distribution'] == 'public-offline'
    for source in json.loads((folder / 'data/sources.json').read_text(encoding='utf-8')):
        if source['path'].endswith('/fares.py'):
            assert not any(token in source['source'] for token in DENIED)
    assert not any(p.suffix.lower() in ('.geojson', '.pbf') for p in folder.rglob('*'))
    assert (folder / 'licenses/MPL-2.0.txt').is_file()
    assert (folder / 'licenses/CPython-3.14.2-license.rst').is_file()
    for name in ('Emscripten-5.0.3-LICENSE.txt','musl-COPYRIGHT.txt','compiler-rt-LICENSE.txt','libunwind-LICENSE.txt'):
        assert (folder / 'licenses' / name).is_file(), name
    assert sum(p.stat().st_size for p in folder.rglob('*') if p.is_file()) < 1024**3


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--web', action='store_true')
    args = parser.parse_args()
    check_source()
    if args.web:
        check_web()
    print('Public source/data boundary verified' + ('; static web verified' if args.web else ''))
