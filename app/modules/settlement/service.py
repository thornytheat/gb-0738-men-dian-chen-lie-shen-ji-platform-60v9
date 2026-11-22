from time import perf_counter

from app.manifest import failure_key, manifest
from app.pipeline import Audit


def _batch(settlement, run_id):
    """The finance-facing batch: per-slot rule version and executed amount, reconciled to a total."""
    slots = settlement['rules']['rows']
    version_total = sum(item['original_cents'] for item in settlement['rules']['versions'])
    slot_total = sum(row['original_cents'] for row in slots)
    return {'run_id': run_id, 'rule_digest': settlement['rules']['digest'],
            'total_cents': settlement['original_cents'], 'version_total_cents': version_total,
            'slot_total_cents': slot_total, 'slot_count': settlement['selected'],
            'versions': settlement['rules']['versions'], 'slots': slots,
            'unversioned': settlement['rules']['unversioned'],
            'balanced': settlement['original_cents'] == version_total == slot_total}


def run_audit(settings, store, options):
    """Build and publish one batch. A failed generation leaves no partial snapshot and is logged.

    Re-running identical effective inputs resolves to the same run_id. The stored batch is then
    re-activated as-is (never rebuilt), so a rerun cannot add a slot or restate an amount.
    """
    start = perf_counter()
    audit = None
    run = None
    try:
        audit = Audit(settings, options)
        run = manifest(audit)
        existing = store.get(run['run_id'], required=False)
        if existing is not None:
            return store.publish(existing)
        settlement = audit.settlement
        run['processing_ms'] = round((perf_counter() - start) * 1000, 3)
        snapshot = {'manifest': run, 'campaign': audit.intake['campaign'], 'intake': audit.intake,
                    'plans': audit.plans, 'photos': audit.photos, 'coverage': audit.coverage,
                    'penalties': audit.penalties, 'settlement': settlement,
                    'batch': _batch(settlement, run['run_id'])}
        return store.publish(snapshot)
    except Exception as error:
        key = run or (failure_key(audit) if audit is not None else failure_key(None, options))
        store.record_failure(key, error)
        raise
