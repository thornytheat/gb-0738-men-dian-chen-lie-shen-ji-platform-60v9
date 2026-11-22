from hashlib import sha256

from app.config import RULE_VERSION
from app.errors import AuditError
from app.manifest import canonical
from app.modules.intake.clock import utc_timestamp
from app.modules.intake.records import load_unique
from app.modules.intake.values import InvalidRow, integer, rule_version_id
from app.modules.penalties.service import RuleCatalog

FIELDS = ('version', 'start_utc', 'end_utc', 'MISSING_SKU', 'WRONG_SKU', 'UNDER_FACINGS', 'PRICE_MISMATCH')
CODES = FIELDS[3:]


def _digest(rows):
    payload = [{key: row[key] for key in ('version', 'start_utc', 'end_utc', 'rates')} for row in rows]
    return sha256(canonical(sorted(payload, key=lambda row: row['version'])).encode()).hexdigest()


def _fallback(campaign):
    entry = {'version': RULE_VERSION, 'start_utc': campaign['start_utc'], 'end_utc': campaign['end_utc'],
             'rates': {code: campaign['penalty_cents'][code] for code in CODES},
             'source': {'id': 'campaign.json', 'file': 'campaign.json', 'line': 1, 'end_line': 1, 'raw': []}}
    return RuleCatalog([entry], _digest([entry]), None, [], 'campaign.json')


def load_rules(table, campaign):
    """Build the dated penalty-rule catalogue.

    ``rules.csv`` is optional. Without it the campaign's single ``penalty_cents`` mapping is one
    version effective for the whole campaign. With it, every row is a version with a half-open
    validity window; a slot is priced with the version covering its visit instant. Windows that
    overlap are reported per slot instead of silently picking a side, and a version id that
    appears twice with different rates rejects the whole catalogue.
    """
    if table is None:
        return _fallback(campaign)

    def convert(row):
        start, end = (utc_timestamp(row[key]) for key in ('start_utc', 'end_utc'))
        if start >= end:
            raise InvalidRow('BAD_INTERVAL')
        return {'version': rule_version_id(row['version']), 'start_utc': start.isoformat(),
                'end_utc': end.isoformat(),
                'rates': {code: integer(row[code], 1, 1_000_000) for code in CODES}}

    conflicts = []
    rows, issues = load_unique(table, convert, ('version',), conflicting=conflicts)
    if conflicts:
        raise AuditError('RULE_VERSION_CONFLICT', '同一规则版本有多套费率', evidence=conflicts)
    if not rows:
        raise AuditError('RULE_CATALOG_EMPTY', '罚则表没有可用版本')
    return RuleCatalog(rows, _digest(rows), table['name'], issues, table['name'])


def rule_for_instant(catalog, instant):
    """Return (single covering version or None, every covering version)."""
    return catalog.select(instant)
