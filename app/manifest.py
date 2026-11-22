import json
from datetime import datetime, timezone
from hashlib import sha256

from app.config import RULE_VERSION


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def manifest(audit):
    parameters = {'dataset': audit.options.dataset, 'sample_size': audit.sample_size}
    key = {'parameters': parameters, 'rule_version': RULE_VERSION,
           'rules': audit.rules.digest,
           'inputs': {name: sha256(raw).hexdigest() for name, raw in audit.bundle.items()}}
    return {**key, 'run_id': sha256(canonical(key).encode()).hexdigest(), 'created_at': now(),
            'version': audit.intake['campaign']['version']}


def failure_key(audit, options=None):
    """A deterministic id for a run that failed before its manifest could be built.

    Deliberately avoids parsing any table: the failure that triggers it is often a parse error.
    """
    if audit is not None:
        parameters = {'dataset': audit.options.dataset, 'sample_size': audit.options.sample_size}
        inputs = {name: sha256(raw).hexdigest() for name, raw in sorted(audit.bundle.items())}
    else:
        parameters = {'dataset': options.dataset, 'sample_size': options.sample_size}
        inputs = {'bundle': 'unavailable'}
    key = {'parameters': parameters, 'inputs': inputs}
    return {**key, 'run_id': sha256(canonical(key).encode()).hexdigest()}
