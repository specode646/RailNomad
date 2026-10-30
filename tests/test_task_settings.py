import json

from PySide6.QtWidgets import QApplication, QLabel, QPushButton
from shiboken6 import isValid

from railnomad.domain.model import Request
from railnomad.domain.search import new_run
from railnomad.storage import encode
from railnomad.ui.main import MainWindow
from railnomad.ui.tasks import setting_sections, TaskSettingsDialog
from railnomad.ui.names import NameCatalog
from railnomad.deletion import delete


def select_task(window, identity):
    window.tasks.refresh()
    row = next(i for i, run in enumerate(window.tasks.runs) if run['id'] == identity)
    window.tasks.table.selectRow(row)


def test_settings_show_the_selected_frozen_request_not_current_planner(store, snapshot):
    app = QApplication.instance() or QApplication([])
    first = Request('a', 'c', min_nights=2, max_nights=3, budget=123.45,
                    departure_date='2026-12-31', consecutive=False, allow_short_hsr=True,
                    first_daylight_limit=None, required_cities=['甲城', '乙城'], required_city_mode='OR',
                    preferred_stations=['b2'], other_preferences='<b>保留原文</b>')
    identity = new_run(store, snapshot, first)
    second = new_run(store, snapshot, Request('a', 'b', min_nights=1, max_nights=1, budget=888))
    original = store.rows('SELECT request FROM runs WHERE id=?', (identity,))[0]['request']
    window = MainWindow(store.home)
    window.planner.fields['budget'].setValue(999)
    select_task(window, identity)
    button = next(b for b in window.tasks.findChildren(QPushButton) if b.text() == '查看设置')
    button.click();app.processEvents()
    dialog = next(d for d in window.findChildren(TaskSettingsDialog) if d.isVisible())
    text = dialog.settings_text
    assert '模拟甲 [a]' in text and '模拟丙 [c]' in text
    assert '¥123.45' in text and '¥999' not in text and '2 — 3 晚' in text
    assert '允许空夜' in text and '至少一个（OR）' in text and '甲城、乙城' in text
    assert '19:30 — 次日 00:30' in text and '06:30 — 次日 00:30' in text
    assert '出发当天最多：不限' in text and '模拟乙二 [b2]' in text
    label = next(label for label in dialog.findChildren(QLabel) if label.text() == '<b>保留原文</b>')
    assert label.textFormat().name == 'PlainText'
    assert dialog.raw_parameters.isReadOnly()
    assert dialog.scroll_area.horizontalScrollBar().maximum() == 0
    assert json.loads(dialog.raw_parameters.toPlainText()) == json.loads(original)
    assert store.rows('SELECT request FROM runs WHERE id=?', (identity,))[0]['request'] == original
    dialog.close();app.processEvents()
    select_task(window, second)
    other = window.tasks.settings()
    assert '¥888.00' in other.settings_text and '¥123.45' not in other.settings_text
    other.close();window.close();app.processEvents()


def test_historical_missing_settings_are_not_filled_with_new_defaults(store, snapshot):
    app = QApplication.instance() or QApplication([])
    identity = new_run(store, snapshot, Request('a', 'c'))
    saved = dict(origin='a', destination='c', min_nights=1, max_nights=2, budget=222,
                 future_option='保留新增参数')
    with store.connect() as db:db.execute('UPDATE runs SET request=? WHERE id=?', (encode(saved), identity))
    window = MainWindow(store.home);select_task(window, identity)
    dialog = window.tasks.settings()
    assert '旅途中每天最多：未记录' in dialog.settings_text
    assert '出发当天最多：未记录' in dialog.settings_text
    assert '允许短高铁：未记录' in dialog.settings_text
    assert json.loads(dialog.raw_parameters.toPlainText()) == saved
    assert '历史任务' in ' '.join(label.text() for label in dialog.findChildren(QLabel))
    dialog.close();window.close();app.processEvents()


def test_disabled_daylight_and_legacy_conversion_are_described_accurately():
    request = Request('a', 'c', limit_daylight=False).to_dict()
    text = str(setting_sections(request, NameCatalog()))
    assert text.count('不限（白天乘车限制已关闭）') == 3
    request.update(limit_daylight=True, max_daylight_on_train=None, min_off_train=600)
    text = str(setting_sections(request, NameCatalog()))
    assert '3 小时（按历史离车时长换算）' in text


def test_open_settings_survive_result_updates_and_close_with_deleted_task(store, snapshot):
    app = QApplication.instance() or QApplication([])
    identity = new_run(store, snapshot, Request('a', 'c'))
    window = MainWindow(store.home);select_task(window, identity)
    dialog = window.tasks.settings();app.processEvents()
    window.sync_catalog(dict(run_ids=[identity]))
    assert dialog.isVisible()
    change = delete(store, 'tasks', [identity]);window.sync_catalog(change);app.processEvents()
    assert not isValid(dialog) or not dialog.isVisible()
    window.close();app.processEvents()


def test_no_selection_and_stale_selection_report_errors(store, snapshot):
    app = QApplication.instance() or QApplication([])
    identity = new_run(store, snapshot, Request('a', 'c'))
    window = MainWindow(store.home);errors=[];window.message=errors.append
    window.tasks.table.clearSelection();window.tasks.table.setCurrentCell(-1, -1)
    assert window.tasks.settings() is None and '请选择' in errors[-1]
    select_task(window, identity)
    delete(store, 'tasks', [identity])
    assert window.tasks.settings() is None and '已删除' in errors[-1]
    window.close();app.processEvents()


def test_missing_snapshot_file_still_allows_saved_parameters_without_creating_file(store, snapshot, tmp_path):
    app = QApplication.instance() or QApplication([])
    identity = new_run(store, snapshot, Request('a', 'c'))
    window = MainWindow(store.home);select_task(window, identity)
    missing = tmp_path/'missing.sqlite3'
    with store.connect() as db:db.execute('UPDATE snapshots SET path=? WHERE id=?', (str(missing), snapshot))
    dialog = window.tasks.settings()
    assert 'a [a]' in dialog.settings_text and not missing.exists()
    dialog.close();window.close();app.processEvents()
