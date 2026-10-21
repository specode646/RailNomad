"""Browser-only adapter. Core rules and search stay in the railnomad package."""
import json
import time
from pathlib import Path

from railnomad.storage import Store, encode
from railnomad.domain.model import Request
from railnomad.domain.search import new_run, search
from railnomad.query import query
from railnomad.resources import risk_warnings

store = None
current_run = None
current_request = None


def initialize(config):
    global store
    home = Path('/data/session')
    home.mkdir(parents=True, exist_ok=True)
    for folder in ('logs', 'jobs'):
        (home / folder).mkdir(exist_ok=True)
    store = Store(home)
    snapshot = dict(config['snapshot'], path='/data/snapshot.sqlite3')
    with store.connect() as db:
        db.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?,?,?,?)',
                   tuple(snapshot[k] for k in ('id', 'tag', 'path', 'metadata', 'quality', 'created')))
        for row in config['evidence']:
            db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?)',
                       tuple(row[k] for k in ('id', 'snapshot', 'kind', 'payload', 'created')))
    store.set_setting('city_mapping:' + snapshot['id'], config['mapping'])
    store.set_setting('active_snapshot', snapshot['id'])
    # MEMFS disk availability is not physical device space. Existing count/time
    # protection stays in the core; all browser data is session-only.
    import railnomad.resources as resources
    resources.check_free = lambda path: None


def validate(values):
    request = Request(**values).validate()
    from railnomad.resources import validate_request
    validate_request(request)
    return dict(request=request.to_dict(), warnings=risk_warnings(request))


def start(values):
    global current_run, current_request
    current_request = Request(**values).validate()
    current_run = new_run(store, store.snapshot()['id'], current_request)
    started = time.perf_counter()
    def progress(**event):
        browser_progress(encode(event))
    audit = search(store, current_run, progress)
    return dict(run_id=current_run, status=audit['status'], audit=audit,
                seconds=time.perf_counter()-started, request=current_request.to_dict())


def summary(payload):
    return {key:payload.get(key) for key in (
        'id','pattern','nights','changes','duration_seconds','comfort_minutes',
        'relaxation_minutes','price_range','known_price','dates','empty_nights',
        'short_hsr','status','price_coverage_complete') } | dict(
        trains=[leg['train'] for leg in payload['legs']],
        start_station=payload['legs'][0]['from_name'],
        end_station=payload['legs'][-1]['to_name'],
        first_departure=payload['legs'][0]['dep'],
        last_arrival=payload['legs'][-1]['arr'])


def results(values):
    if not current_run:
        raise ValueError('请先完成一次搜索')
    page = max(0, int(values.get('page', 0)))
    filters = dict(values.get('filters') or {})
    grouped = not bool(values.get('pattern'))
    if not grouped:
        filters['pattern'] = values['pattern']
    size = 12 if grouped else 8
    rows, total, variants = query(store, current_run, filters, page, size,
                                  grouped=grouped, sort=values.get('sort','comfort'))
    if not grouped:
        rows = [summary(json.loads(row['payload'])) for row in rows]
    return dict(items=rows, total_items=total, total_variants=variants,
                page=page, page_size=size, total_pages=(total+size-1)//size)


def detail(identity):
    rows = store.rows('SELECT payload FROM results WHERE id=? AND run_id=?', (identity,current_run))
    if not rows:
        raise ValueError('当前任务中找不到该方案')
    payload = json.loads(rows[0]['payload'])
    return dict(payload=payload, request=current_request.to_dict())


def dispatch(operation, values):
    if operation == 'initialize':
        initialize(values)
        return dict(ready=True)
    if operation == 'validate':
        return validate(values)
    if operation == 'start':
        return start(values)
    if operation == 'results':
        return results(values)
    if operation == 'detail':
        return detail(values['id'])
    raise ValueError('不支持的本机操作')
