"""Regression tests for App Store / first-run deployment behavior."""
from __future__ import annotations

import base64
import copy
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from python_organizer_local_llm.config_defaults import DEFAULT_CONFIG
from python_organizer_local_llm.database import Database
from python_organizer_local_llm.file_types import from_extensions
from python_organizer_local_llm.nextcloud import NextcloudClient
from python_organizer_local_llm.appapi_auth import outgoing_headers
from python_organizer_local_llm.settings import EnvironmentSettings, SettingsService


def _settings_system(tmp_path):
    db = Database(None)
    db.path = tmp_path / "settings.db"
    db.initialize()
    config = copy.deepcopy(DEFAULT_CONFIG)
    classifier = SimpleNamespace(
        config=config,
        base_url="",
        model="",
        timeout=0,
        temperature=0,
        max_content_chars=0,
        paperless_enabled=False,
        paperless_inbox="/inbox",
        paperless_never_send=set(),
        paperless_prefer_send=set(),
        global_instructions="",
        folder_rules=[],
    )
    scanner = SimpleNamespace(
        scan_paths=list(config["scanner"]["scan_paths"]),
        exclude_paths=list(config["scanner"]["exclude_paths"]),
        allowed_extensions=set(),
        allow_sensitive=True,
    )
    nextcloud = SimpleNamespace(
        scan_paths=[], exclude_paths=[], allowed_extensions=set(), allow_sensitive=True,
        ocr_enabled=True, ocr_max_pages=10, _ocr_cache={},
    )
    organizer = SimpleNamespace(
        config_file=None,
        runtime_settings=EnvironmentSettings(),
        database=db,
        classifier=classifier,
        scanner=scanner,
        nextcloud=nextcloud,
        paperless_enabled=False,
        paperless_inbox="/inbox",
    )
    return organizer, SettingsService(organizer)


def test_builtin_defaults_match_pre_config_yaml_policy(tmp_path):
    _, service = _settings_system(tmp_path)
    settings = service.get()
    assert service.is_configured() is False
    assert settings["ollama_url"] == ""
    assert settings["model"] == ""
    assert DEFAULT_CONFIG["paperless"]["never_send"] == [
        "resume", "cv", "curriculum vitae", "cover letter", "portfolio",
        "source_code", "project", "template",
    ]
    assert settings["paperless_never_send"] == [
        "resume", "cv", "curriculum_vitae", "cover_letter", "portfolio",
        "source_code", "project", "template",
    ]
    assert settings["paperless_prefer_send"] == [
        "receipt", "invoice", "statement", "tax", "insurance", "contract", "warranty",
    ]
    assert settings["file_types"] == from_extensions([
        "pdf", "txt", "md", "docx", "odt", "rtf", "log", "csv", "json", "xml", "xlsx",
    ])




def test_app_level_appapi_auth_allows_empty_user_for_lifecycle_registration():
    runtime = EnvironmentSettings(
        nextcloud_url="http://nextcloud.local", app_secret="shared-secret"
    )
    token = outgoing_headers(runtime, "")["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode() == ":shared-secret"

def test_appapi_mode_does_not_require_basic_auth_credentials():
    runtime = EnvironmentSettings(
        nextcloud_url="http://nextcloud.local",
        app_secret="shared-secret",
        app_user="",
    )
    client = NextcloudClient(None, runtime_settings=runtime)
    assert client.auth_mode == "appapi"
    assert client.session.auth is None
    assert client.username == ""

    client.set_active_user("admin")
    token = client.session.headers["AUTHORIZATION-APP-API"]
    assert base64.b64decode(token).decode() == "admin:shared-secret"
    assert client._dav_url("/AI Inbox/test.pdf").startswith(
        "http://nextcloud.local/remote.php/dav/files/admin/"
    )


def test_standalone_local_mode_still_uses_app_password():
    runtime = EnvironmentSettings(
        nextcloud_url="http://nextcloud.local",
        nextcloud_username="developer",
        nextcloud_app_password="app-password",
    )
    client = NextcloudClient(None, runtime_settings=runtime)
    assert client.auth_mode == "basic"
    assert client.session.auth == ("developer", "app-password")

    with pytest.raises(RuntimeError, match="NEXTCLOUD_APP_PASSWORD"):
        NextcloudClient(None, runtime_settings=EnvironmentSettings(
            nextcloud_url="http://nextcloud.local", nextcloud_username="developer"
        ))


def test_appstore_manifest_does_not_request_nextcloud_user_password():
    root = Path(__file__).resolve().parents[1]
    tree = ET.parse(root / "appinfo" / "info.xml")
    names = [element.findtext("name") for element in tree.findall(".//environment-variables/variable")]
    assert "NEXTCLOUD_USERNAME" not in names
    assert "NEXTCLOUD_APP_PASSWORD" not in names
    assert {"OLLAMA_URL", "OLLAMA_MODEL", "PAPERLESS_ENABLED", "INBOX_PATH"} <= set(names)



def test_appstore_manifest_allows_public_static_assets_without_opening_admin_api():
    root = Path(__file__).resolve().parents[1]
    tree = ET.parse(root / "appinfo" / "info.xml")
    routes = []
    for route in tree.findall(".//external-app/routes/route"):
        routes.append({
            "url": route.findtext("url"),
            "verb": route.findtext("verb"),
            "access": route.findtext("access_level"),
        })

    assert {
        "url": "^/static/.*$",
        "verb": "GET",
        "access": "PUBLIC",
    } in routes

    admin_catchall = next(
        route for route in routes
        if route["access"] == "ADMIN" and route["url"].startswith("^(?!/static/)")
    )
    assert "/static/" in admin_catchall["url"]


def test_static_assets_are_served_with_correct_mime_types():
    from fastapi.testclient import TestClient

    # Import after setting a minimal local-development environment so the app
    # can initialize without AppAPI or a live Ollama instance.
    import os
    os.environ.setdefault("NEXTCLOUD_URL", "http://nextcloud.local")
    os.environ.setdefault("NEXTCLOUD_USERNAME", "developer")
    os.environ.setdefault("NEXTCLOUD_APP_PASSWORD", "app-password")

    from exapp.main import app

    with TestClient(app) as client:
        css = client.get("/static/app.css")
        js = client.get("/static/app.js")

    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert js.status_code == 200
    assert "javascript" in js.headers["content-type"]

def test_first_run_ui_is_triggered_by_incomplete_required_settings():
    root = Path(__file__).resolve().parents[1]
    script = (root / "exapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "First-time setup" in script
    assert "Connect AI Organizer to Ollama" in script
    assert "data.configured === false" in script
    assert "Save & continue" in script
