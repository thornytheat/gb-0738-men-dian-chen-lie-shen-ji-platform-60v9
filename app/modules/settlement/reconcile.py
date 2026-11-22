from collections import Counter, defaultdict

from app.errors import AuditError
from app.modules.intake.clock import utc_timestamp

UNPRICED = ('RULE_MISSING', 'RULE_OVERLAP')


class _Report:
    """Read-only view of the rule summary carried in ``judgments['rules']``."""

    def __init__(self, report):
        self.digest = report['digest']
        self.catalog = {item['version']: item for item in report['versions']}


def _implicit_catalog(judgments):
    return _Report(judgments['rules'])


def ids(rows):
    return [row['source']['id'] for row in rows]


def row_ledger(table, valid, issues):
    original = Counter(ids(table['rows']) + ids(table['quarantine']))
    if original != Counter(ids(valid) + ids(issues)) or sum(original.values()) != table['input']:
        raise AuditError('SOURCE_LEDGER_MISMATCH', '原件行号对账失败', 500, table['name'])
    return {'input': table['input'], 'accepted': len(valid), 'isolated': len(issues),
            'blank': table['blank'], 'repeated_headers': table['repeated_headers']}


def rule_ledger(judgments, catalog):
    """Every priced slot names the version covering its visit and uses that version's rate."""
    known = catalog.catalog
    rows = judgments['rows']
    for row in rows:
        visit = row['visit']
        covering = [] if visit is None else sorted(
            version for version, item in known.items()
            if utc_timestamp(item['start_utc']) <= utc_timestamp(visit['at_utc']) < utc_timestamp(item['end_utc']))
        version = row['rule_version']
        if version is not None and (visit is None or version not in covering):
            raise AuditError('RULE_VERSION_MISMATCH', '柜位规则版本与巡检时刻不符', 500, row['key'])
        if row['status'] in ('VIOLATION', 'COMPLIANT') and (len(covering) != 1 or version != covering[0]):
            raise AuditError('RULE_VERSION_MISMATCH', '柜位规则版本与巡检时刻不符', 500, row['key'])
        if row['status'] == 'ISOLATED' and row['reason'] in UNPRICED:
            expected = 'RULE_MISSING' if not covering else 'RULE_OVERLAP'
            if row['reason'] != expected:
                raise AuditError('RULE_VERSION_MISMATCH', '柜位规则隔离原因不符', 500, row['key'])
        if row['status'] == 'VIOLATION':
            applied = row['applied']
            if not applied or applied['code'] not in known[version]['rates']:
                raise AuditError('RULE_AMOUNT_MISMATCH', '柜位缺少适用规则费率', 500, row['key'])
            if applied['cents'] != known[version]['rates'][applied['code']]:
                raise AuditError('RULE_AMOUNT_MISMATCH', '柜位执行额与适用规则费率不符', 500, row['key'])
    summary = []
    for version in sorted(known, key=lambda code: (utc_timestamp(known[code]['start_utc']), code)):
        item = known[version]
        members = [row for row in rows if row['rule_version'] == version]
        summary.append({'version': version, 'start_utc': item['start_utc'], 'end_utc': item['end_utc'],
                        'rates': item['rates'], 'source': item['source'],
                        'counts': {key: sum(row['status'] == key for row in members)
                                   for key in ('COMPLIANT', 'VIOLATION', 'PENDING', 'ISOLATED')},
                        'violations': sum(row['status'] == 'VIOLATION' for row in members),
                        'original_cents': sum(row['original_cents'] for row in members)})
    if sum(item['original_cents'] for item in summary) != sum(row['original_cents'] for row in rows):
        raise AuditError('RULE_AMOUNT_MISMATCH', '规则版本分组合计与柜位总额不符', 500)
    return summary


def reconcile(intake, plans, photos, coverage, judgments, rules=None):
    catalog = rules or _implicit_catalog(judgments)
    files = {**{name: intake[name] for name in ('stores', 'slots', 'visits')},
             'plans': plans['plans'], 'aliases': plans['alias_rows'],
             'photos': photos['photos'], 'observations': photos['observations']}
    quarantine = sorted(intake['quarantine'] + plans['quarantine'] + photos['quarantine'],
                        key=lambda row: (row['source']['file'], row['source']['line']))
    by_file = defaultdict(list)
    for mark in quarantine:
        by_file[mark['source']['file']].append(mark)
    sources = {key: row_ledger(intake['tables'][key], valid, by_file[f'{key}.csv']) for key, valid in files.items()}
    rows = judgments['rows']
    counts = judgments['counts']
    statuses = Counter(row['status'] for row in rows)
    evidence = Counter(row['evidence_status'] for row in rows)
    # A rule-based isolation only reclassifies an otherwise verified slot; it never changes the
    # coverage denominator. Reconcile evidence first, then map back to the decided statuses.
    rule_isolated = [row for row in rows if row['evidence_status'] == 'VERIFIED' and row['reason'] in UNPRICED]
    if (statuses != Counter(counts) or Counter(row['key'] for row in rows) != Counter(row['key'] for row in coverage['slots'])
            or len(rows) != coverage['selected'] or len(rows) != sum(counts.values())
            or coverage['selected'] + coverage['unfilled'] != coverage['target']
            or counts['COMPLIANT'] + counts['VIOLATION'] + len(rule_isolated) != coverage['verified']
            or evidence['PENDING'] != coverage['counts']['PENDING']
            or evidence['ISOLATED'] != coverage['counts']['ISOLATED']
            or any(row['evidence_status'] != row['status'] for row in rows
                   if row['status'] in ('PENDING', 'ISOLATED') and row['reason'] not in UNPRICED)
            or any(row['status'] == 'PENDING' and row['reason'] in UNPRICED for row in rows)
            or any(row['applied'] and row['status'] != 'VIOLATION' for row in rows)):
        raise AuditError('COVERAGE_LEDGER_MISMATCH', '柜位抽检对账失败', 500)
    strata = []
    for group in coverage['strata']:
        group_rows = [row for row in rows if row['group'] == group['code']]
        result = Counter(row['status'] for row in group_rows)
        evidence_group = Counter(row['evidence_status'] for row in group_rows)
        group_rule_isolated = [row for row in group_rows
                               if row['evidence_status'] == 'VERIFIED' and row['reason'] in UNPRICED]
        if (len(group_rows) != group['selected']
                or result['COMPLIANT'] + result['VIOLATION'] + len(group_rule_isolated) != group['verified']
                or evidence_group['PENDING'] != group['pending']
                or evidence_group['ISOLATED'] != group['isolated']):
            raise AuditError('COVERAGE_LEDGER_MISMATCH', '店组抽检对账失败', 500, group['code'])
        strata.append({'code': group['code'], 'counts': {key: result[key] for key in counts},
                       'original_cents': sum(row['original_cents'] for row in group_rows)})
    if any(row['original_cents'] != (row['applied']['cents'] if row['applied'] else 0)
           or bool(row['applied']) != (row['status'] == 'VIOLATION') for row in rows):
        raise AuditError('AMOUNT_LEDGER_MISMATCH', '柜位罚额与依据不符', 500)
    total = sum(row['original_cents'] for row in rows)
    if total != sum(item['original_cents'] for item in strata):
        raise AuditError('AMOUNT_LEDGER_MISMATCH', '罚额分组对账失败', 500)
    versions = rule_ledger(judgments, catalog)
    slots = [{'key': row['key'], 'store': row['store'], 'slot': row['slot'], 'group': row['group'],
              'status': row['status'], 'reason': row['reason'], 'visit_at_utc': row['visit']['at_utc'] if row['visit'] else None,
              'rule_version': row['rule_version'], 'rule_candidates': row['rule_candidates'],
              'applied_rule': row['applied']['code'] if row['applied'] else None,
              'candidates': row['candidates'], 'original_cents': row['original_cents']} for row in rows]
    return {'balanced': True, 'target': coverage['target'], 'selected': len(rows), 'unfilled': coverage['unfilled'],
            'counts': counts, 'original_cents': total, 'strata': strata, 'sources': sources,
            'rules': {'digest': catalog.digest, 'versions': versions, 'rows': slots,
                      'unversioned': [row['key'] for row in rows if row['rule_version'] is None]},
            'quarantine': quarantine, 'reason_counts': dict(sorted(Counter(row['reason'] for row in quarantine).items()))}
