"""Fetch exact LGPL source archives, verifying SHA-256 before accepting them."""
import hashlib
import json
import urllib.request
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'third-party'
folder = root / 'sources'
folder.mkdir(exist_ok=True)
for item in json.loads((root / 'source-manifest.json').read_text(encoding='utf-8')):
    name = item['file']
    assert Path(name).name == name
    target = folder / name
    if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == item['sha256']:
        continue
    temporary = target.with_suffix('.partial')
    errors = []
    for url in (item['origin'], item.get('mirror')):
        if not url:
            continue
        try:
            with urllib.request.urlopen(url, timeout=120) as response, temporary.open('wb') as stream:
                import shutil
                shutil.copyfileobj(response, stream)
            with temporary.open('rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == item['sha256'], 'SHA-256 mismatch'
            temporary.replace(target)
            print('Verified', name)
            break
        except Exception as error:
            errors.append(str(error))
            temporary.unlink(missing_ok=True)
    else:
        raise RuntimeError('Cannot obtain required corresponding source: ' + name + ': ' + '; '.join(errors))
