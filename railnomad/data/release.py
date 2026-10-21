"""Public release discovery and cancellable streaming download."""
import hashlib
import json
import time
import os
import urllib.error
import urllib.request
from pathlib import Path

API = 'https://api.github.com/repos/wensimehrp/chinese-railway-gtfs/releases/latest'


class RetryLater(Exception):
    pass


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel():
        raise Cancelled('操作已取消；原快照保持可用')


def latest(store, cancel=lambda: False):
    if os.environ.get('RAILNOMAD_OFFLINE') == '1':
        raise ValueError('当前处于离线模式，请联网后再检查更新')
    if store.setting('release_retry_after',0)>time.time():
        raise RetryLater('GitHub 正在限流等待期，请稍后重试；本地数据不受影响')
    headers = {'User-Agent': 'RailNomad/0.1', 'Accept': 'application/vnd.github+json'}
    cached = store.setting('release_cache')
    if cached and cached.get('etag'):
        headers['If-None-Match'] = cached['etag']
    for attempt in range(3):
        check_cancel(cancel)
        try:
            with urllib.request.urlopen(urllib.request.Request(API, headers=headers), timeout=30) as response:
                release = json.load(response)
                store.set_setting('release_cache', {'etag': response.headers.get('ETag'), 'release': release})
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 304 and cached:
                release = cached['release']
                break
            if exc.code in (403, 429):
                headers=exc.headers or {}
                try:
                    wait=float(headers.get('Retry-After','60'))
                    reset=float(headers.get('X-RateLimit-Reset','0'))
                    if not 0<=wait<86400:wait=60
                    deadline=max(time.time()+wait,reset)
                except (TypeError,ValueError):deadline=time.time()+60
                store.set_setting('release_retry_after',deadline)
                raise RetryLater('GitHub 请求受限，请稍后重试；本地数据不受影响') from exc
            if exc.code < 500 or attempt == 2:
                raise
        except (TimeoutError, urllib.error.URLError):
            if attempt == 2:
                raise
        for _ in range(5 * (attempt + 1)):
            check_cancel(cancel)
            time.sleep(.2)
    if release.get('draft') or release.get('prerelease'):
        raise ValueError('远端返回的不是正式发布版，请检查来源')
    assets = [a for a in release['assets'] if a['name'].lower().endswith('.zip')]
    if not assets:
        raise ValueError('正式版中没有 GTFS ZIP 数据附件')
    return {'release': release, 'assets': assets}


def download(asset, home, progress=lambda **kw: None, cancel=lambda: False):
    if os.environ.get('RAILNOMAD_OFFLINE') == '1':
        raise ValueError('当前处于离线模式，无法下载；已导入快照仍可搜索')
    url = asset['browser_download_url']
    if not url.startswith('https://'):
        raise ValueError('只接受 HTTPS 数据附件')
    target = Path(home) / 'downloads' / f"asset-{asset['id']}.zip"
    partial = target.with_suffix('.part')
    digest = hashlib.sha256()
    count = 0
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'RailNomad/0.1'}), timeout=30) as response, partial.open('wb') as out:
            if hasattr(response,'geturl') and not response.geturl().lower().startswith('https://'):
                raise ValueError('数据附件重定向到了非 HTTPS 地址')
            while block := response.read(1024 * 1024):
                check_cancel(cancel)
                out.write(block)
                digest.update(block)
                count += len(block)
                if count > asset['size']:
                    raise ValueError('下载大小超过 Release 声明，已停止并清理临时文件')
                progress(stage='下载', current=count, total=asset['size'])
        if count != asset['size']:
            raise ValueError('下载大小与 Release 声明不符')
        upstream = asset.get('digest')
        if upstream and upstream != 'sha256:' + digest.hexdigest():
            raise ValueError('下载文件 SHA-256 与上游摘要不符')
        partial.replace(target)
        return target
    finally:
        partial.unlink(missing_ok=True)
