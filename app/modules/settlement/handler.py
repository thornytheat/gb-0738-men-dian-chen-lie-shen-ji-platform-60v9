from typing import Annotated

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.config import Options
from app.modules.appeals.service import with_appeals
from app.modules.settlement.service import run_audit
from app.pipeline import Audit

router = APIRouter(prefix='/api/settlement')
RunId = Annotated[str, Path(pattern=r'^[0-9a-f]{64}$')]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class BatchAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    actor: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    note: Text


@router.get('')
def preview(request: Request, options: Annotated[Options, Query()]):
    return Audit(request.app.state.settings, options).settlement


@router.post('/runs')
def run(request: Request, options: Options):
    snapshot, reused = run_audit(request.app.state.settings, request.app.state.store, options)
    return {'reused': reused, **with_appeals(snapshot)}


@router.get('/runs')
def list_runs(request: Request):
    return {'runs': request.app.state.store.history(), 'failures': request.app.state.store.failures()}


@router.get('/runs/{run_id}')
def read_run(request: Request, run_id: RunId):
    return with_appeals(request.app.state.store.get(run_id))


@router.get('/runs/{run_id}/batch')
def read_batch(request: Request, run_id: RunId):
    snapshot = request.app.state.store.get(run_id)
    return {'manifest': snapshot['manifest'], 'status': snapshot['status'],
            'superseded_by': snapshot['superseded_by'],
            'batch': {**snapshot['batch'], 'run_id': run_id}, 'appeals': with_appeals(snapshot)['appeals']}


@router.get('/runs/{run_id}/history')
def read_history(request: Request, run_id: RunId):
    request.app.state.store.get(run_id)
    return {'history': request.app.state.store.history(run_id),
            'failures': request.app.state.store.failures(run_id)}


@router.post('/runs/{run_id}/rollback')
def rollback(request: Request, run_id: RunId, body: BatchAction):
    store = request.app.state.store
    target = store.get(run_id)
    replacement = None
    if target['status'] == 'ACTIVE':
        alternatives = [row for row in store.history() if row['run_id'] != run_id
                        and row['status'] in ('SUPERSEDED', 'ACTIVE')]
        replacement = alternatives[-1]['run_id'] if alternatives else None
    result = store.rollback(run_id, f'{body.actor}：{body.note}', replacement)
    return {'run': {key: result[key] for key in ('status', 'superseded_by')},
            'restored': replacement, 'history': store.history(run_id)}
