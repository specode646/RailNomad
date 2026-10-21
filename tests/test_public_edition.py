import json
from pathlib import Path

import pytest

from railnomad.fares import PublicFareClient, price_route, price_routes, estimate
from railnomad.paths import data_home


def test_automatic_lookup_is_unavailable_even_when_called_directly():
    for function in (PublicFareClient, price_route, price_routes):
        with pytest.raises(ValueError, match='公开发行版'):
            function(None)


def test_manual_student_estimates_remain_available():
    assert estimate(200, seat='硬卧', emu=False, hard_seat=100)['high'] == 150


def test_public_home_ignores_full_edition_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('RAILNOMAD_HOME', str(tmp_path / 'private'))
    monkeypatch.setenv('RAILNOMAD_PUBLIC_HOME', str(tmp_path / 'public'))
    assert data_home() == tmp_path / 'public'
    assert not (tmp_path / 'private').exists()


def test_first_start_installs_only_reviewed_data(tmp_path):
    from railnomad.public_data import install_public_snapshot
    from railnomad.storage import Store
    install_public_snapshot(tmp_path)
    store = Store(tmp_path)
    assert store.setting('active_snapshot')
    assert len(store.setting('city_mapping:' + store.snapshot()['id'])) == 3146
    assert all(row['kind'] == 'city_membership' for row in store.rows('SELECT * FROM evidence'))
    assert store.rows('SELECT * FROM runs') == []
    original = store.snapshot()['id']
    install_public_snapshot(tmp_path)
    assert store.snapshot()['id'] == original


def test_public_site_has_no_business_backend_transport():
    root = Path(__file__).resolve().parents[1]
    providers = (root / 'web/providers.js').read_text(encoding='utf-8')
    assert 'fetch(' not in providers and '/api/v1' not in providers
    assert json.loads((root / 'public-data/release.json').read_text(encoding='utf-8'))['evidence'] == []
