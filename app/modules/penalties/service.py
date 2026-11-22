from collections import Counter

from app.modules.intake.clock import utc_timestamp
from app.modules.penalties.rules import candidates


def judge(coverage, campaign, catalog):
    """Decide each drawn slot with the rule version effective at its visit instant."""
    rows, counts, evidence = [], Counter(), Counter()
    for entry in coverage['slots']:
        visit = entry['visit']
        selected, covering = (catalog.select(visit['at_utc']) if visit else (None, []))
        version = selected['version'] if selected else None
        matched = []
        if entry['evidence_status'] == 'VERIFIED':
            if selected is None:
                status = 'ISOLATED'
                reason = 'RULE_MISSING' if not covering else 'RULE_OVERLAP'
            else:
                matched = candidates(entry['plan'], entry['evidence'], selected['rates'])
                status = 'VIOLATION' if matched else 'COMPLIANT'
                reason = matched[0]['code'] if matched else entry['reason']
        else:
            status, reason = entry['evidence_status'], entry['reason']
        applied = matched[0] if matched else None
        evidence[entry['evidence_status']] += 1
        rows.append({'key': entry['key'], 'store': entry['store'], 'slot': entry['slot'], 'group': entry['group'],
                     'category': entry['category'], 'status': status, 'reason': reason, 'candidates': matched,
                     'applied': applied, 'original_cents': applied['cents'] if applied else 0,
                     'evidence_status': entry['evidence_status'],
                     'rule_version': version, 'rule_candidates': [item['version'] for item in covering],
                     'rule_source': selected['source'] if selected else None,
                     'visit': visit, 'plan': entry['plan'], 'plan_candidates': entry['plan_candidates'],
                     'evidence': entry['evidence'], 'roster_source': entry['source']})
        counts[status] += 1
    return {'rows': rows, 'counts': {key: counts[key] for key in ('COMPLIANT', 'VIOLATION', 'PENDING', 'ISOLATED')},
            'evidence_counts': {key: evidence[key] for key in ('VERIFIED', 'PENDING', 'ISOLATED')},
            'rules': catalog.report()}


class RuleCatalog:
    """Dated penalty rules. ``select`` returns one version for an instant, or all covering ones."""

    def __init__(self, rows, digest, source, issues, fallback_source):
        self.rows = rows
        self.digest = digest
        self.source = source
        self.issues = issues
        self.catalog = {row['version']: row for row in rows}
        self.fallback_source = fallback_source

    def select(self, instant):
        stamp = utc_timestamp(instant)
        covering = sorted((row for row in self.rows
                           if utc_timestamp(row['start_utc']) <= stamp < utc_timestamp(row['end_utc'])),
                          key=lambda row: (utc_timestamp(row['start_utc']), row['version']))
        return (covering[0] if len(covering) == 1 else None), covering

    def report(self):
        versions = [{'version': row['version'], 'start_utc': row['start_utc'], 'end_utc': row['end_utc'],
                     'rates': row['rates'], 'source': row['source']}
                    for row in sorted(self.rows, key=lambda row: (utc_timestamp(row['start_utc']), row['version']))]
        return {'versions': versions, 'digest': self.digest, 'source': self.source, 'issues': self.issues}
