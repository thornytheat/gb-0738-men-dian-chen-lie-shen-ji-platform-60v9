from copy import deepcopy

import pytest

from app.errors import AuditError
from app.modules.penalties.catalog import load_rules
from app.modules.penalties.service import judge
from conftest import locate_or_fail


def test_each_slot_is_priced_with_the_rule_effective_at_its_visit(field):
    rows = {row['key']: row for row in field.penalties['rows']}
    # 001 visits on 11-07 (old rule), 005 on 11-11 (new rule). The campaign crosses the 11-10 cut.
    assert rows['002:A03']['visit']['at_utc'] == '2026-11-09T00:00:00+00:00'
    assert rows['002:A03']['rule_version'] == '2026.11-r1'
    assert rows['002:A03']['original_cents'] == 15000
    assert rows['005:A01']['visit']['at_utc'] == '2026-11-11T08:00:00+00:00'
    assert rows['005:A01']['rule_version'] == '2026.11-r2'
    assert rows['005:A01']['original_cents'] == 11000
    assert rows['005:A01']['applied']['proof']['skus'] == ['MALT-900']


def test_batch_total_is_the_sum_of_slot_versions_and_not_all_old_or_all_new(field):
    sheet = field.settlement
    assert sheet['original_cents'] == 31500
    assert sum(row['original_cents'] for row in sheet['rules']['rows']) == 31500
    by_version = {item['version']: item['original_cents'] for item in sheet['rules']['versions']}
    assert by_version == {'2026.11-r1': 20500, '2026.11-r2': 11000}
    assert sheet['rules']['unversioned'] == []
    # All-old would be 29 500 (zeros the post-cut 11 000 gap). All-new would reprice pre-cut visits.
    assert sheet['original_cents'] not in (29500, 15000 + 11000 + 6500 + 5000)


def test_unadopted_candidates_and_rule_source_remain_inspectable(field):
    row = locate_or_fail(field.penalties['rows'], key='001:A03')
    assert [(c['code'], c['cents']) for c in row['candidates']] == [('UNDER_FACINGS', 5500), ('PRICE_MISMATCH', 4000)]
    assert row['applied']['code'] == 'UNDER_FACINGS'
    versions = {item['version']: item for item in field.settlement['rules']['versions']}
    assert versions['2026.11-r1']['source']['id'] == 'rules.csv:2'
    assert versions['2026.11-r2']['rates']['MISSING_SKU'] == 18000


def test_rules_table_is_optional_and_falls_back_to_campaign(field):
    campaign = field.intake['campaign']
    implicit = load_rules(None, campaign)
    assert [row['version'] for row in implicit.rows] == ['2026.11-r1']
    assert implicit.select('2026-11-20T00:00:00Z')[0]['rates'] == campaign['penalty_cents']


def test_effective_point_boundary_is_half_open(field):
    r1, r2 = '2026.11-r1', '2026.11-r2'
    assert field.rules.select('2026-11-09T23:59:59Z')[0]['version'] == r1
    assert field.rules.select('2026-11-10T00:00:00Z')[0]['version'] == r2


def test_overlapping_rule_windows_isolate_the_slot_instead_of_pricing(field):
    field.bundle['rules.csv'] += b'2026.11-r3,2026-11-08T00:00:00Z,2026-11-12T00:00:00Z,18000,11000,6500,5000\r\n'
    field.refresh()
    row = locate_or_fail(field.penalties['rows'], key='002:A03')
    assert (row['status'], row['reason'], row['original_cents']) == ('ISOLATED', 'RULE_OVERLAP', 0)
    assert row['rule_candidates'] == ['2026.11-r1', '2026.11-r3']


def test_gap_in_rule_windows_isolates_the_slot_as_rule_missing(field):
    field.bundle['rules.csv'] = (b'version,start_utc,end_utc,MISSING_SKU,WRONG_SKU,UNDER_FACINGS,PRICE_MISMATCH\n'
                                 b'2026.11-r1,2026-11-01T00:00:00Z,2026-11-08T00:00:00Z,15000,9000,5500,4000\n')
    field.refresh()
    row = locate_or_fail(field.penalties['rows'], key='002:A03')
    assert (row['status'], row['reason'], row['original_cents']) == ('ISOLATED', 'RULE_MISSING', 0)
    assert row['rule_candidates'] == []


def test_rule_gap_still_produces_a_balanced_batch(field):
    field.bundle['rules.csv'] = (b'version,start_utc,end_utc,MISSING_SKU,WRONG_SKU,UNDER_FACINGS,PRICE_MISMATCH\n'
                                 b'2026.11-r1,2026-11-01T00:00:00Z,2026-11-08T00:00:00Z,15000,9000,5500,4000\n')
    field.refresh()
    sheet = field.settlement
    # Rule isolation keeps the coverage denominator; the batch stays reconciled and 0 for unpriced slots.
    assert sheet['balanced'] is True
    assert sheet['rules']['rows']
    assert all(row['original_cents'] == 0 for row in sheet['rules']['rows'] if row['reason'] == 'RULE_MISSING')
    assert sheet['original_cents'] == sum(row['original_cents'] for row in sheet['rules']['rows'])
    isolated = [row for row in field.penalties['rows'] if row['reason'] == 'RULE_MISSING']
    assert len(isolated) == 9
    assert field.coverage['verified'] == sheet['counts']['COMPLIANT'] + sheet['counts']['VIOLATION'] + len(isolated)


def test_conflicting_rule_rates_for_one_version_reject_the_catalogue(field):
    field.bundle['rules.csv'] += b'2026.11-r1,2026-11-20T00:00:00Z,2026-11-25T00:00:00Z,1,1,1,1\r\n'
    field.refresh()
    with pytest.raises(AuditError) as error:
        _ = field.rules
    assert error.value.code == 'RULE_VERSION_CONFLICT'


def test_amount_that_disagrees_with_the_version_rate_fails_reconciliation(field):
    from app.modules.settlement.reconcile import reconcile
    judgments = deepcopy(field.penalties)
    target = next(row for row in judgments['rows'] if row['status'] == 'VIOLATION')
    target['applied']['cents'] += 1
    target['original_cents'] += 1
    with pytest.raises(AuditError) as error:
        reconcile(field.intake, field.plans, field.photos, field.coverage, judgments, field.rules)
    assert error.value.code == 'RULE_AMOUNT_MISMATCH'


def test_rule_digest_changes_when_rates_change_but_not_when_inputs_are_reordered(field):
    before = field.rules.digest
    table = deepcopy(field.intake['tables']['rules'])
    table['rows'].reverse()
    assert load_rules(table, field.intake['campaign']).digest == before
    table['rows'][0]['MISSING_SKU'] = '99999'
    assert load_rules(table, field.intake['campaign']).digest != before
