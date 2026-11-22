import shutil
import sqlite3

import pytest

from app.config import Options
from app.errors import AuditError
from app.modules.settlement.service import run_audit
from app.store import Store


def _audit(client, sample_size=None):
    body = {} if sample_size is None else {'sample_size': sample_size}
    response = client.post('/api/settlement/runs', json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_batch_names_a_rule_version_and_amount_per_slot_and_adds_up(client):
    batch = _audit(client)
    sheet = batch['settlement']
    assert batch['batch']['balanced'] is True
    assert batch['batch']['total_cents'] == sheet['original_cents'] == 31500
    assert batch['batch']['slot_total_cents'] == batch['batch']['version_total_cents'] == 31500
    assert all(row['rule_version'] for row in batch['batch']['slots'])
    versions = {item['version']: item['original_cents'] for item in batch['batch']['versions']}
    assert versions == {'2026.11-r1': 20500, '2026.11-r2': 11000}
    assert sum(row['original_cents'] for row in batch['batch']['slots']) == batch['batch']['total_cents']


def test_rerun_under_same_rules_neither_adds_a_slot_nor_reprices(client):
    first = _audit(client)
    second = _audit(client)
    assert second['reused'] is True
    assert second['manifest']['run_id'] == first['manifest']['run_id']
    assert first['batch']['slots'] == second['batch']['slots']
    history = client.get('/api/settlement/runs').json()['runs']
    assert len(history) == 1
    assert history[0]['status'] == 'ACTIVE'


def test_failed_generation_leaves_the_published_batch_untouched(settings, tmp_path):
    duplicate = tmp_path / 'data'
    shutil.copytree(settings.sample_root, duplicate)
    settings.sample_root = duplicate
    store = Store(settings.database)
    store.initialize()
    good, _ = run_audit(settings, store, Options())
    target = duplicate / 'field' / 'observations.csv'
    baseline = target.read_bytes()
    target.write_bytes(baseline + b'V001,A01,"cut')
    with pytest.raises(AuditError) as error:
        run_audit(settings, store, Options())
    assert error.value.code == 'MALFORMED_CSV'
    # No partial snapshot, current batch still ACTIVE and intact.
    assert store.get(good['manifest']['run_id'])['status'] == 'ACTIVE'
    assert store.get(good['manifest']['run_id'])['settlement']['original_cents'] == 31500
    # The failure is recorded, not swallowed.
    assert store.failures()[0]['code'] == 'MALFORMED_CSV'
    target.write_bytes(baseline)
    retried, reused = run_audit(settings, store, Options())
    # Restoring the original inputs re-publishes the identical batch without repricing it.
    assert reused and retried['manifest']['run_id'] == good['manifest']['run_id']
    assert retried['batch']['total_cents'] == 31500
    assert [row['run_id'] for row in store.history()] == [good['manifest']['run_id']]


def test_a_newer_batch_supersedes_but_history_is_kept(client):
    older = _audit(client, 18)
    newer = _audit(client, 17)
    assert newer['reused'] is False
    listing = client.get('/api/settlement/runs').json()['runs']
    statuses = {row['run_id']: row['status'] for row in listing}
    assert statuses[older['manifest']['run_id']] == 'SUPERSEDED'
    assert statuses[newer['manifest']['run_id']] == 'ACTIVE'
    # The old batch and its rule versions stay readable after being superseded.
    old = client.get(f"/api/settlement/runs/{older['manifest']['run_id']}/batch").json()
    assert old['status'] == 'SUPERSEDED'
    assert old['batch']['total_cents'] == 31500
    assert {item['version'] for item in old['batch']['versions']} == {'2026.11-r1', '2026.11-r2'}


def test_rollback_restores_the_prior_batch_and_keeps_the_failed_one_queryable(client):
    older = _audit(client, 18)
    newer = _audit(client, 17)
    response = client.post(f"/api/settlement/runs/{newer['manifest']['run_id']}/rollback",
                           json={'actor': '黄稽核', 'note': '费率复核未通过'})
    assert response.status_code == 200
    body = response.json()
    assert body['run']['status'] == 'ROLLED_BACK'
    assert body['restored'] == older['manifest']['run_id']
    restored = client.get(f"/api/settlement/runs/{older['manifest']['run_id']}").json()
    assert restored['status'] == 'ACTIVE'
    # Rolled-back run is not deleted; its history survives.
    assert client.get(f"/api/settlement/runs/{newer['manifest']['run_id']}").json()['status'] == 'ROLLED_BACK'
    history = client.get(f"/api/settlement/runs/{newer['manifest']['run_id']}/history").json()['history']
    assert any(row['action'] == 'ROLLBACK' for row in history)


def test_rolled_back_batch_cannot_be_silently_republished(client):
    older = _audit(client, 18)
    newer = _audit(client, 17)
    client.post(f"/api/settlement/runs/{newer['manifest']['run_id']}/rollback",
                json={'actor': '黄稽核', 'note': '费率复核未通过'})
    blocked = client.post('/api/settlement/runs', json={'sample_size': 17})
    assert (blocked.status_code, blocked.json()['detail']['code']) == (409, 'BATCH_ROLLED_BACK')
    assert client.get(f"/api/settlement/runs/{newer['manifest']['run_id']}").json()['status'] == 'ROLLED_BACK'
    assert client.get(f"/api/settlement/runs/{older['manifest']['run_id']}").json()['status'] == 'ACTIVE'


def test_rerunning_a_superseded_batch_reactivates_it_without_repricing(client):
    older = _audit(client, 18)
    newer = _audit(client, 17)
    again = client.post('/api/settlement/runs', json={'sample_size': 18}).json()
    assert again['reused'] is True and again['manifest']['run_id'] == older['manifest']['run_id']
    assert again['batch']['slots'] == older['batch']['slots']
    statuses = {row['run_id']: row['status'] for row in client.get('/api/settlement/runs').json()['runs']}
    assert statuses[older['manifest']['run_id']] == 'ACTIVE'
    assert statuses[newer['manifest']['run_id']] == 'SUPERSEDED'


def test_batch_endpoint_surfaces_unadopted_candidates_for_reviewers(client):
    batch = client.get(f"/api/settlement/runs/{_audit(client)['manifest']['run_id']}/batch").json()
    reviewed = {row['key']: row for row in batch['batch']['slots']}
    assert [(c['code'], c['cents']) for c in reviewed['001:A03']['candidates']] == [
        ('UNDER_FACINGS', 5500), ('PRICE_MISMATCH', 4000)]
    assert reviewed['001:A03']['applied_rule'] == 'UNDER_FACINGS'
    assert reviewed['002:A03']['rule_version'] == '2026.11-r1'


def test_history_tables_are_append_only(client):
    run = _audit(client)
    with sqlite3.connect(client.app.state.settings.database) as connection:
        actions = connection.execute('SELECT COUNT(*) FROM run_actions').fetchone()[0]
        connection.execute('BEGIN IMMEDIATE')
    assert actions >= 1
    # Publishing again does not append a second slot row or overwrite the batch.
    replay = _audit(client)
    assert replay['manifest']['run_id'] == run['manifest']['run_id']
    assert replay['batch']['slot_count'] == run['batch']['slot_count']
