from app.modules.coverage.sampling import choose_slots

STATUSES = ('VERIFIED', 'PENDING', 'ISOLATED')
ISOLATING_EVIDENCE = ('PHOTO_REUSED', 'PHOTO_ID_CONFLICT', 'OBSERVATION_CONFLICT', 'BAD_OBSERVATION')


def evidence_of(binding):
    if binding is None:
        return {'products': {}, 'completed_sources': [], 'photos': [], 'conflicting_skus': []}
    return {'products': binding['products'], 'completed_sources': binding['completed_sources'],
            'photos': binding['photos'], 'conflicting_skus': binding['conflicting_skus']}


def classify(decision, binding):
    if decision['plan'] is None:
        return ('PENDING' if decision['reason'] == 'VISIT_MISSING' else 'ISOLATED'), decision['reason']
    if binding is None:
        return 'PENDING', 'MISSING_PHOTO'
    if binding['reason'] is None:
        return 'VERIFIED', None
    if binding['reason'] in ISOLATING_EVIDENCE:
        return 'ISOLATED', binding['reason']
    return 'PENDING', binding['reason']


def coverage(intake, plans, photos, target):
    drawn, nominal = choose_slots(intake, target)
    decisions = {(row['store'], row['slot']): row for row in plans['decisions']}
    bindings = {(row['visit'], row['slot']): row for row in photos['bindings']}
    slots = []
    for row in drawn:
        decision = decisions[row['store'], row['slot']]
        visit = decision.get('visit')
        binding = bindings.get((visit['visit'], row['slot'])) if visit else None
        status, reason = classify(decision, binding)
        slots.append({'key': row['key'], 'store': row['store'], 'slot': row['slot'], 'group': row['group'],
                      'category': row['category'], 'score': row['score'], 'source': row['source'],
                      'visit': visit, 'plan': decision['plan'], 'plan_candidates': decision['candidates'],
                      'evidence_status': status, 'reason': reason, 'evidence': evidence_of(binding)})
    strata = []
    for nominal_group in nominal:
        members = [row for row in slots if row['group'] == nominal_group['code']]
        counts = {status: sum(row['evidence_status'] == status for row in members) for status in STATUSES}
        strata.append({'code': nominal_group['code'], 'title': nominal_group['title'],
                       'weight': nominal_group['weight'], 'pool': nominal_group['pool'],
                       'target': nominal_group['target'], 'selected': nominal_group['selected'],
                       'unfilled': nominal_group['unfilled'],
                       'ranked_candidates': nominal_group['ranked_candidates'],
                       'verified': counts['VERIFIED'], 'pending': counts['PENDING'],
                       'isolated': counts['ISOLATED'], 'gap': nominal_group['target'] - counts['VERIFIED']})
    return {'target': target, 'selected': len(slots), 'unfilled': sum(group['unfilled'] for group in strata),
            'verified': sum(row['evidence_status'] == 'VERIFIED' for row in slots),
            'counts': {status: sum(row['evidence_status'] == status for row in slots) for status in STATUSES},
            'strata': strata, 'slots': slots}
