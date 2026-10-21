"""Initialize a separate public-edition home from the reviewed timetable only."""
import hashlib
import json
import shutil
import sys
from pathlib import Path

from .storage import Store


def install_public_snapshot(home):
    store = Store(home)
    if store.setting('active_snapshot'):
        return
    root = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))
    folder = root / 'public-data'
    if not folder.is_dir():
        return
    record = json.loads((folder / 'release.json').read_text(encoding='utf-8'))
    source = folder / 'snapshot.sqlite3'
    with source.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != record['sha256']:
            raise ValueError('随包运行图校验失败，请重新下载完整发行包')
    snapshot = dict(record['snapshot'])
    destination = Path(home) / 'snapshots' / snapshot['id'] / 'gtfs.sqlite3'
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    snapshot['path'] = str(destination)
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?)',
                   tuple(snapshot[key] for key in ('id', 'tag', 'path', 'metadata', 'quality', 'created')))
    store.activate(snapshot['id'])
