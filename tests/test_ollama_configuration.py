"""Ollama first-run configuration and analysis-gating regressions."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from exapp.routes import settings as settings_routes
from exapp.routes.analyze import analyze_file
from python_organizer_local_llm.config_defaults import DEFAULT_CONFIG
from python_organizer_local_llm.database import Database
from python_organizer_local_llm.settings import EnvironmentSettings, SettingsService


def build_service(tmp_path):
    database = Database(None)
    database.path = tmp_path / 'settings.db'
    database.initialize()
    config = copy.deepcopy(DEFAULT_CONFIG)
    classifier = SimpleNamespace(
        config=config, base_url='', model='', timeout=180, temperature=0,
        max_content_chars=8000, paperless_enabled=False, paperless_inbox='/inbox',
        paperless_never_send=set(), paperless_prefer_send=set(),
        global_instructions='', folder_rules=[],
    )
    scanner = SimpleNamespace(
        scan_paths=list(config['scanner']['scan_paths']),
        exclude_paths=list(config['scanner']['exclude_paths']),
        allowed_extensions=set(), allow_sensitive=True,
    )
    nextcloud = SimpleNamespace(
        auth_mode='basic', scan_paths=[], exclude_paths=[], allowed_extensions=set(),
        allow_sensitive=True, ocr_enabled=True, ocr_max_pages=10, _ocr_cache={},
    )
    organizer = SimpleNamespace(
        config_file=None, runtime_settings=EnvironmentSettings(), database=database,
        classifier=classifier, scanner=scanner, nextcloud=nextcloud,
        paperless_enabled=False, paperless_inbox='/inbox',
    )
    return SettingsService(organizer)


def test_settings_can_save_ollama_url_before_model(tmp_path):
    service = build_service(tmp_path)
    values = service.get()
    values['ollama_url'] = 'http://192.168.1.2:11434/'
    values['model'] = ''
    saved = service.save(values)
    assert saved['ollama_url'] == 'http://192.168.1.2:11434'
    assert saved['model'] == ''
    assert service.is_configured() is False


def test_unsaved_ollama_url_can_be_tested_and_discovered(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    app = FastAPI()
    app.state.settings = service
    app.include_router(settings_routes.router)

    calls = []

    class Response:
        content = b'{}'
        def __init__(self, data): self._data = data
        def raise_for_status(self): return None
        def json(self): return self._data

    def fake_get(url, timeout):
        calls.append((url, timeout))
        if url.endswith('/api/version'):
            return Response({'version': '0.12.0'})
        if url.endswith('/api/tags'):
            return Response({'models': [{'name': 'qwen2.5:7b'}, {'name': 'llama3.1:8b'}]})
        raise AssertionError(url)

    monkeypatch.setattr(settings_routes.requests, 'get', fake_get)
    with TestClient(app) as client:
        tested = client.post('/api/settings/ollama/test', json={'url': 'http://ollama.local:11434'})
        models = client.post('/api/settings/ollama/models', json={'url': 'http://ollama.local:11434'})

    assert tested.status_code == 200
    assert tested.json() == {'connected': True, 'version': '0.12.0'}
    assert models.status_code == 200
    assert models.json()['models'] == ['qwen2.5:7b', 'llama3.1:8b']
    assert all(call[0].startswith('http://ollama.local:11434/api/') for call in calls)


def test_model_pull_uses_ollama_pull_endpoint(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    app = FastAPI()
    app.state.settings = service
    app.include_router(settings_routes.router)
    captured = {}

    class Response:
        ok = True
        status_code = 200
        text = ''
        def json(self): return {'status': 'success'}
        def raise_for_status(self): return None

    def fake_post(url, json, timeout):
        captured.update(url=url, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(settings_routes.requests, 'post', fake_post)
    with TestClient(app) as client:
        response = client.post('/api/settings/ollama/pull', json={
            'url': 'http://ollama.local:11434', 'model': 'qwen2.5:7b'
        })

    assert response.status_code == 202
    payload = response.json()
    assert payload['job_id']
    assert captured['url'] == 'http://ollama.local:11434/api/pull'
    assert captured['json'] == {'model': 'qwen2.5:7b', 'stream': False}
    assert captured['timeout'] >= 3600
    status = client.get(f"/api/settings/ollama/pull/{payload['job_id']}")
    assert status.status_code == 200
    assert status.json()['status'] == 'completed'
    assert status.json()['message'] == 'Model download completed: qwen2.5:7b.'


def test_model_pull_failure_is_available_to_ui(tmp_path, monkeypatch):
    service = build_service(tmp_path)
    app = FastAPI()
    app.state.settings = service
    app.include_router(settings_routes.router)

    class Response:
        ok = False
        status_code = 500
        text = ''
        def json(self):
            return {'error': 'pull model manifest: file does not exist'}

    monkeypatch.setattr(settings_routes.requests, 'post', lambda *args, **kwargs: Response())
    with TestClient(app) as client:
        response = client.post('/api/settings/ollama/pull', json={
            'url': 'http://ollama.local:11434', 'model': 'missing:model'
        })
        assert response.status_code == 202
        status = client.get(f"/api/settings/ollama/pull/{response.json()['job_id']}")

    assert status.status_code == 200
    payload = status.json()
    assert payload['status'] == 'failed'
    assert payload['error'].startswith('Model not found: missing:model.')
    assert 'file does not exist' in payload['error']


def test_analysis_api_rejects_unconfigured_ollama():
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        organizer=SimpleNamespace(), settings=SimpleNamespace(is_configured=lambda: False)
    )))
    with pytest.raises(HTTPException) as exc:
        analyze_file(file_id='42', request=request)
    assert exc.value.status_code == 409
    assert 'Ollama URL and model' in exc.value.detail
