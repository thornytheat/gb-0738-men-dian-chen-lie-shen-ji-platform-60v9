from collections import defaultdict
from hashlib import sha256

SEED_SEPARATOR = '\0'


def apportion(groups, target):
    weights = {group['code']: group['weight'] for group in groups}
    total = sum(weights.values())
    shares = {code: target * weight / total for code, weight in weights.items()}
    quotas = {code: int(share) for code, share in shares.items()}
    order = sorted(weights, key=lambda code: (-(shares[code] - quotas[code]), code))
    for code in order[:target - sum(quotas.values())]:
        quotas[code] += 1
    return quotas


def slot_score(seed, store, slot):
    return sha256(f'{seed}{SEED_SEPARATOR}{store}{SEED_SEPARATOR}{slot}'.encode()).hexdigest()


def rank_slots(intake, quotas):
    seed = intake['campaign']['seed']
    group_of = {row['store']: row['group'] for row in intake['stores']}
    by_group = defaultdict(list)
    for row in intake['slots']:
        by_group[group_of[row['store']]].append(
            {'key': f"{row['store']}:{row['slot']}", 'store': row['store'], 'slot': row['slot'],
             'group': group_of[row['store']], 'category': row['category'], 'source': row['source'],
             'score': slot_score(seed, row['store'], row['slot'])})
    chosen = []
    for group in intake['campaign']['groups']:
        ranked = sorted(by_group.get(group['code'], []), key=lambda row: (row['score'], row['key']))
        chosen.append({'code': group['code'], 'title': group['title'], 'weight': group['weight'],
                       'pool': len(ranked), 'target': quotas[group['code']], 'ranked_candidates': ranked})
    return chosen


def choose_slots(intake, target):
    quotas = apportion(intake['campaign']['groups'], target)
    strata = rank_slots(intake, quotas)
    rows = []
    for stratum in strata:
        drawn = stratum['ranked_candidates'][:stratum['target']]
        stratum['selected'] = len(drawn)
        stratum['unfilled'] = max(0, stratum['target'] - len(drawn))
        rows.extend(drawn)
    return rows, strata
