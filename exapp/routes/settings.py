"""Administrator settings and local Ollama discovery helpers.

The route is ADMIN-only via ``appinfo/info.xml``. Ollama connection helpers accept
an unsaved URL so first-run setup can test/discover before the settings row is
complete. Model pulls run outside the request lifecycle and expose a short-lived
status record so the UI can report completion or failures instead of only logging
them server-side.
"""
from __future__ import annotations

import logging
from threading import Lock
from time import time
from uuid import uuid4

import requests
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

from python_organizer_local_llm.file_types import catalog
from python_organizer_local_llm.appapi_auth import user_from_incoming_header

router = APIRouter(prefix='/api/settings', tags=['settings'])
log = logging.getLogger('settings.routes')

_PULL_JOBS: dict[str, dict] = {}
_PULL_JOBS_LOCK = Lock()
_PULL_JOB_LIMIT = 100


class SettingsUpdate(BaseModel):
    settings: dict


class OllamaURLRequest(BaseModel):
    url: str


class OllamaPullRequest(OllamaURLRequest):
    model: str


def _service(request):
    service = request.app.state.settings
    runtime = getattr(service.organizer, "runtime_settings", None)
    headers = getattr(request, "headers", {}) or {}
    if runtime and runtime.app_secret:
        user_id = user_from_incoming_header(
            headers.get("AUTHORIZATION-APP-API"), runtime.app_secret
        )
        if user_id:
            service.organizer.nextcloud.set_active_user(user_id)
            service.organizer.database.save_app_state("service_user", user_id)
    return service


def _ollama_url(service, raw_url: str) -> str:
    try:
        return service.normalize_ollama_url(raw_url, required=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _ollama_model(service, raw_model: str) -> str:
    try:
        return service.normalize_ollama_model(raw_model, required=True)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _request_timeout(service, *, discovery: bool = False) -> int:
    configured = int(service.get().get('timeout') or 180)
    return min(configured, 10) if discovery else configured


def _models_for(url: str, timeout: int) -> list[str]:
    response = requests.get(f"{url}/api/tags", timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return [
        item['name']
        for item in data.get('models', [])
        if isinstance(item, dict) and item.get('name')
    ]


def _response_detail(response) -> str:
    """Return Ollama's useful error text without exposing an HTML proxy page."""
    try:
        data = response.json()
        if isinstance(data, dict):
            detail = data.get('error') or data.get('message') or data.get('detail')
            if detail:
                return str(detail).strip()
    except (ValueError, TypeError):
        pass
    text = str(getattr(response, 'text', '') or '').strip()
    if text and not text.lstrip().startswith('<'):
        return text[:500]
    return ''


def _pull_error_message(model: str, response) -> str:
    detail = _response_detail(response)
    lowered = detail.casefold()
    not_found_markers = (
        'not found', 'file does not exist', 'pull model manifest',
        'manifest does not exist', 'model manifest',
    )
    if response.status_code == 404 or any(marker in lowered for marker in not_found_markers):
        message = f'Model not found: {model}. Check the model name and tag.'
        return f'{message} Ollama: {detail}' if detail else message
    suffix = f': {detail}' if detail else ''
    return f'Ollama could not download {model} (HTTP {response.status_code}){suffix}'


def _set_pull_job(job_id: str, **updates) -> None:
    with _PULL_JOBS_LOCK:
        job = _PULL_JOBS.get(job_id)
        if job is not None:
            job.update(updates)


def _new_pull_job(url: str, model: str) -> str:
    job_id = uuid4().hex
    with _PULL_JOBS_LOCK:
        if len(_PULL_JOBS) >= _PULL_JOB_LIMIT:
            oldest = min(_PULL_JOBS, key=lambda key: _PULL_JOBS[key].get('created_at', 0))
            _PULL_JOBS.pop(oldest, None)
        _PULL_JOBS[job_id] = {
            'job_id': job_id,
            'status': 'queued',
            'model': model,
            'url': url,
            'message': f'Download queued for {model}.',
            'error': '',
            'created_at': time(),
        }
    return job_id


def _pull_model(job_id: str, url: str, model: str, timeout: int) -> None:
    """Run a potentially long Ollama model pull and publish its final status."""
    _set_pull_job(job_id, status='running', message=f'Downloading {model}…')
    try:
        response = requests.post(
            f"{url}/api/pull",
            json={'model': model, 'stream': False},
            # Large local models can take a long time to download. Keep the
            # browser/AppAPI request short by doing this as a background task.
            timeout=max(timeout, 3600),
        )
        if not response.ok:
            error = _pull_error_message(model, response)
            _set_pull_job(job_id, status='failed', error=error, message=error)
            log.error("Ollama model pull failed: %s (%s): %s", model, url, error)
            return
        _set_pull_job(
            job_id,
            status='completed',
            message=f'Model download completed: {model}.',
            error='',
        )
        log.info("Ollama model pull completed: %s (%s)", model, url)
    except requests.RequestException as exc:
        error = f'Model download failed: {exc}'
        _set_pull_job(job_id, status='failed', error=error, message=error)
        log.exception("Ollama model pull failed: %s (%s)", model, url)
    except Exception as exc:  # Defensive: background exceptions must reach the UI.
        error = f'Model download failed: {exc}'
        _set_pull_job(job_id, status='failed', error=error, message=error)
        log.exception("Ollama model pull failed: %s (%s)", model, url)


@router.get('')
def get_settings(request: Request):
    service = _service(request)
    return {'settings': service.get(), 'configured': service.is_configured(),
            'auth_mode': service.organizer.nextcloud.auth_mode,
            'automation': service.organizer.database.get_automation_run(),
            'file_types_catalog': catalog()}


@router.put('')
def save_settings(body: SettingsUpdate, request: Request):
    try:
        service = _service(request)
        settings = service.save(body.settings)
        return {'settings': settings, 'configured': service.is_configured()}
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post('/ollama/test')
def test_ollama(body: OllamaURLRequest, request: Request):
    service = _service(request)
    url = _ollama_url(service, body.url)
    try:
        response = requests.get(f"{url}/api/version", timeout=_request_timeout(service, discovery=True))
        response.raise_for_status()
        data = response.json() if response.content else {}
        return {'connected': True, 'version': str(data.get('version') or '')}
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f'Ollama connection failed: {exc}') from exc


@router.post('/ollama/models')
def discover_models(body: OllamaURLRequest, request: Request):
    service = _service(request)
    url = _ollama_url(service, body.url)
    try:
        return {
            'connected': True,
            'models': _models_for(url, _request_timeout(service, discovery=True)),
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f'Ollama model discovery failed: {exc}') from exc


@router.post('/ollama/pull', status_code=202)
def pull_model(body: OllamaPullRequest, request: Request, background_tasks: BackgroundTasks):
    service = _service(request)
    url = _ollama_url(service, body.url)
    model = _ollama_model(service, body.model)
    job_id = _new_pull_job(url, model)
    background_tasks.add_task(_pull_model, job_id, url, model, _request_timeout(service))
    return {
        'started': True,
        'job_id': job_id,
        'model': model,
        'status': 'queued',
        'message': f'Model download started: {model}.',
    }


@router.get('/ollama/pull/{job_id}')
def pull_model_status(job_id: str, request: Request):
    _service(request)  # Preserve AppAPI user binding for this ADMIN-only endpoint.
    with _PULL_JOBS_LOCK:
        job = _PULL_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail='Model download status is no longer available.')
        # Never expose the configured Ollama URL through the status response.
        return {key: value for key, value in job.items() if key not in {'url', 'created_at'}}


# Backward-compatible saved-settings discovery endpoint. New UI code uses the
# POST endpoint above so an administrator can test an unsaved URL.
@router.get('/models')
def list_models(request: Request):
    service = _service(request)
    values = service.get()
    url = _ollama_url(service, values.get('ollama_url', ''))
    try:
        return {
            'connected': True,
            'models': _models_for(url, _request_timeout(service, discovery=True)),
        }
    except (requests.RequestException, ValueError, TypeError) as exc:
        raise HTTPException(status_code=502, detail=f'Ollama connection failed: {exc}') from exc


@router.get('/folders')
def list_folders(request: Request):
    client = _service(request).organizer.nextcloud
    try:
        folders = ['/']
        for item in client._propfind('/', depth='infinity'):
            path = item.get('path')
            if item.get('is_directory') and path and path not in folders:
                folders.append(path)
        return {'folders': sorted(folders, key=str.casefold)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail='Unable to retrieve Nextcloud folders') from exc
