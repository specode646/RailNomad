import json
import pytest

from railnomad.deletion import delete
from railnomad.domain.model import Request
from railnomad.domain.search import new_run, search
from railnomad.query import query
from railnomad.tourism import build, ready, score
from railnomad.storage import now, encode


def test_ranking_cache_and_deletion(store, snapshot):
    run = new_run(store, snapshot, Request('a', 'c', min_nights=2, max_nights=2))
    search(store, run)
    assert not ready(store, run)
    result = build(store, run)
    assert result['total'] > 0 and ready(store, run)
    rows, count, _ = query(store, run, sort='recommendation')
    groups, _, _ = query(store, run, grouped=True, sort='recommendation')
    assert count == len(rows) and groups
    with store.connect() as db:
        for group in groups:
            expected = db.execute('''SELECT r.id FROM results r JOIN tourism_rank t ON t.result_id=r.id
                WHERE r.run_id=? AND r.pattern=? ORDER BY t.score DESC,r.id LIMIT 1''', (run, group['pattern'])).fetchone()[0]
            assert group['representative'] == expected
    assert build(store, run)['unchanged']
    delete(store, 'results', [run], [rows[0]['id']])
    assert ready(store, run)
    assert build(store, run)['unchanged']
    remaining, _, _ = query(store, run, sort='recommendation')
    assert rows[0]['id'] not in {r['id'] for r in remaining}


def test_favorite_filter_cache_updates(store, snapshot):
    run = new_run(store, snapshot, Request('a', 'c', min_nights=2, max_nights=2))
    search(store, run)
    build(store, run)
    all_rows, _, _ = query(store, run, sort='recommendation')
    assert all_rows
    filters = {'favorite': True}
    assert query(store, run, filters, grouped=True, sort='recommendation')[1] == 0
    with store.connect() as db:
        db.execute('INSERT INTO favorites VALUES(?,?)', (all_rows[0]['id'], now()))
    groups, count, _ = query(store, run, filters, grouped=True, sort='recommendation')
    assert count == 1 and groups[0]['representative'] == all_rows[0]['id']


def test_more_daylight_and_more_tourism_improve_score():
    base = {'pattern': ['起点', '中途', '终点'], 'legs': [{'from':'a','to':'b','arr': 10*3600}, {'from':'b','to':'c','dep': 13*3600}], 'comfort_minutes': 0}
    values = {'中途': 100}
    stations={'a':'起点','b':'中途','c':'终点'}
    good = score(base, {}, values, 0, station_cities=stations)
    higher = score(base, {}, {'中途': 500}, 0, station_cities=stations)
    assert higher > good
    worse = dict(base, comfort_minutes=120)
    assert score(worse, {}, values, 0, station_cities=stations) < good


def test_comfort_penalty_is_gentle_and_capped():
    base={'legs':[], 'comfort_minutes':0}
    normal=score(base,{}, {},0)
    for minutes, penalty in [(20, 20/12), (60, 5), (240, 20), (480, 20)]:
        assert normal-score(dict(base,comfort_minutes=minutes),{}, {},0)==pytest.approx(penalty,abs=1e-5)


def test_transfer_only_at_origin_does_not_count_as_sightseeing():
    p={'legs':[{'from':'a','to':'b','arr':36000},{'from':'b','to':'c','dep':64800}], 'comfort_minutes':0}
    station={'a':'起点','b':'起点','c':'终点'}
    assert score(p,{}, {'起点': 1000},0,station_cities=station)==score(p,{}, {},0,station_cities=station)


def test_transport_data_and_reference_price_are_independent():
    p={'legs':[{'from':'a','to':'b','arr':36000},{'from':'b','to':'c','dep':64800}], 'comfort_minutes':0}
    station={'a':'起点','b':'中途','c':'终点'}
    low=score(p,{}, {'中途':100},0,{'中途':.2},station)
    high=score(dict(p,price=9999),{}, {'中途':100},0,{'中途':.8},station)
    assert high > low
    assert score(dict(p,price=9999),{}, {'中途':100},0,{'中途':.2},station)==low


def test_stay_at_night_does_not_gain_tourism_and_long_stay_is_capped():
    station={'a':'起点','b':'中途','c':'终点'}
    p={'legs':[{'from':'a','to':'b','arr':21*3600},{'from':'b','to':'c','dep':30*3600}], 'comfort_minutes':0}
    night=score(p,{}, {'中途':1000},0,station_cities=station)
    assert night == score(p,{}, {'中途':10},0,station_cities=station)
    p['legs'][0]['arr']=7*3600
    p['legs'][1]['dep']=15*3600
    eight=score(p,{}, {'中途':1000},0,station_cities=station)
    p['legs'][1]['dep']=19*3600
    assert score(p,{}, {'中途':1000},0,station_cities=station)==eight


def test_relaxed_result_can_outrank_a_comfortable_result(store, snapshot):
    run = new_run(store, snapshot, Request('a', 'c', min_nights=2, max_nights=2))
    search(store, run)
    rows = store.rows('SELECT id,payload FROM results WHERE run_id=? ORDER BY id', (run,))
    assert len(rows) >= 2
    with store.connect() as db:
        for index, row in enumerate(rows):
            p = json.loads(row['payload'])
            p['comfort_minutes'] = 0 if index == 0 else 30
            if index == 0:
                p['legs'] = p['legs'][:1]
            db.execute('UPDATE results SET payload=? WHERE id=?', (encode(p), row['id']))
    build(store, run)
    ordered, _, _ = query(store, run, sort='recommendation')
    grouped, _, _ = query(store, run, grouped=True, sort='recommendation')
    assert ordered[0]['id'] == rows[1]['id']
    assert grouped[0]['representative'] == rows[1]['id']


def test_other_sort_choices_remain_available_in_the_desktop(store, snapshot):
    from PySide6.QtWidgets import QApplication
    from railnomad.ui.main import MainWindow
    app = QApplication.instance() or QApplication([])
    window = MainWindow(store.home)
    options = [window.results.sort.itemData(i) for i in range(window.results.sort.count())]
    assert options[:3] == ['recommendation', 'comfort', 'changes']
    assert 'price' in options and 'duration' in options
    window.close()
    app.processEvents()


def test_previous_algorithm_index_is_rebuilt(store, snapshot):
    run = new_run(store, snapshot, Request('a', 'c', min_nights=2, max_nights=2))
    search(store, run)
    build(store, run)
    with store.connect() as db:
        db.execute("UPDATE tourism_rank_state SET version='tourism-recommendation-v3' WHERE run_id=?", (run,))
    assert not ready(store, run)
    assert build(store, run)['indexed'] > 0
    assert ready(store, run)
