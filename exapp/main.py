from __future__ import annotations

import logging
from pathlib import Path

import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from exapp.routes.analyze import router as analyze_router
from exapp.routes.apply import router as apply_router
from exapp.routes.file_action import router as file_action_router
from exapp.routes.dashboard import router as dashboard_router
from exapp.routes.settings import router as settings_router
from exapp.automation import AutomationScheduler
from python_organizer_local_llm.settings import SettingsService, load_environment_settings
from python_organizer_local_llm.appapi_auth import outgoing_headers, user_from_incoming_header
from python_organizer_local_llm.file_types import app_action_mimes
from python_organizer_local_llm.organizer import Organizer, configure_logging

from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.exception_handlers import http_exception_handler

RUNTIME_SETTINGS = load_environment_settings(load_env_file=True)
APP_ID = RUNTIME_SETTINGS.app_id
APP_VERSION = RUNTIME_SETTINGS.app_version
AA_VERSION = RUNTIME_SETTINGS.app_api_version
APP_SECRET = RUNTIME_SETTINGS.app_secret
NEXTCLOUD_URL = RUNTIME_SETTINGS.require_exapp_url()
CONFIG_FILE = RUNTIME_SETTINGS.config_file

configure_logging(RUNTIME_SETTINGS.debug_logging)
log = logging.getLogger("exapp")

app = FastAPI(title="AI Organizer", version=APP_VERSION)


@app.exception_handler(StarletteHTTPException)
async def log_http_error(request, exc):
    log.error(
        "HTTP ERROR: %s %s | Status: %s | Detail: %s",
        request.method,
        request.url.path,
        exc.status_code,
        exc.detail,
    )

    return await http_exception_handler(request, exc)


@app.middleware("http")
async def bind_appapi_user_context(request: Request, call_next):
    # Browser/API routes are ADMIN-only in info.xml. Binding here ensures the
    # first dashboard request has a DAV user before Settings is opened.
    if APP_SECRET:
        user_id = user_from_incoming_header(
            request.headers.get("AUTHORIZATION-APP-API"), APP_SECRET
        )
        if user_id and hasattr(app.state, "organizer"):
            app.state.organizer.nextcloud.set_active_user(user_id)
            app.state.organizer.database.save_app_state("service_user", user_id)
    return await call_next(request)


@app.middleware("http")
async def log_all_requests(request: Request, call_next):
    log.info(
        "Incoming request: %s %s",
        request.method,
        request.url.path,
    )

    response = await call_next(request)

    log.info(
        "Response: %s %s -> %s",
        request.method,
        request.url.path,
        response.status_code,
    )

    return response


app.state.organizer = Organizer(
    config_file=CONFIG_FILE, runtime_settings=RUNTIME_SETTINGS
)
app.state.organizer.database.initialize()
# In AppAPI mode the user context arrives through authenticated proxy requests.
# Restore the last administrator/service user so scheduled scans keep working
# after container replacement or restart. APP_USER remains a local/manual override.
if RUNTIME_SETTINGS.app_secret and not app.state.organizer.nextcloud.username:
    saved_user = app.state.organizer.database.load_app_state("service_user", "")
    if saved_user:
        app.state.organizer.nextcloud.set_active_user(saved_user)
app.state.settings = SettingsService(app.state.organizer)
app.state.scheduler = AutomationScheduler(app)


@app.on_event('startup')
def start_automation():
    app.state.scheduler.start()


@app.on_event('shutdown')
def stop_automation():
    app.state.scheduler.shutdown()


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.include_router(file_action_router)
app.include_router(analyze_router)
app.include_router(apply_router)
app.include_router(dashboard_router)
app.include_router(settings_router)


def _bind_appapi_user(request: Request) -> str | None:
    """Capture the authenticated proxied user for AppAPI-backed file access."""
    user_id = user_from_incoming_header(
        request.headers.get("AUTHORIZATION-APP-API"), APP_SECRET
    )
    if user_id:
        app.state.organizer.nextcloud.set_active_user(user_id)
        app.state.organizer.database.save_app_state("service_user", user_id)
    return user_id


def _appapi_headers(user_id: str | None = None):
    user_id = user_id or app.state.organizer.nextcloud.username
    headers = outgoing_headers(RUNTIME_SETTINGS, user_id)
    headers["Accept"] = "application/json"
    return headers


def _ocs(method, path, json=None, allow_404=False):
    url = f"{NEXTCLOUD_URL.rstrip('/')}/ocs/v2.php{path}"

    response = requests.request(
        method,
        url,
        headers=_appapi_headers(),
        json=json,
        timeout=30,
    )

    log.info(
        "AppAPI %s %s -> HTTP %s",
        method,
        path,
        response.status_code,
    )

    if response.text:
        log.debug("AppAPI response: %s", response.text)

    if allow_404 and response.status_code == 404:
        return None

    response.raise_for_status()

    if not response.content:
        return None

    try:
        data = response.json()
    except ValueError:
        return response.text

    # OCS can return HTTP 200 while reporting an application-level error.
    meta = (
        data.get("ocs", {}).get("meta", {})
        if isinstance(data, dict)
        else {}
    )

    status_code = meta.get("statuscode")

    if status_code not in (None, 100, 200):
        raise RuntimeError(
            f"AppAPI OCS failure: "
            f"{meta.get('status')} "
            f"{status_code} "
            f"{meta.get('message')}"
        )

    return data


def register_ui() -> None:
    log.info("Registering AI Organizer UI")

    # Remove stale registrations first.
    try:
        _ocs(
            "DELETE",
            "/apps/app_api/api/v1/ui/files-actions-menu",
            json={"name": "ai_organize"},
            allow_404=True,
        )
    except Exception:
        log.warning("Could not remove old file action", exc_info=True)

    try:
        _ocs(
            "DELETE",
            "/apps/app_api/api/v1/ui/top-menu",
            json={"name": "organizer"},
            allow_404=True,
        )
    except Exception:
        log.warning("Could not remove old top menu", exc_info=True)

    # Register Top Menu.
    top_menu_result = _ocs(
        "POST",
        "/apps/app_api/api/v1/ui/top-menu",
        json={
            "name": "organizer",
            "displayName": "AI Organizer",
            "adminRequired": 1,
        },
    )

    log.info("Top menu registration result: %s", top_menu_result)

    # Register CSS for the Top Menu.
    style_result = _ocs(
        "POST",
        "/apps/app_api/api/v1/ui/style",
        json={
            "type": "top_menu",
            "name": "organizer",
            "path": "static/app",
        },
    )

    log.info("Top menu style registration result: %s", style_result)

    # Register JavaScript for the Top Menu.
    script_result = _ocs(
        "POST",
        "/apps/app_api/api/v1/ui/script",
        json={
            "type": "top_menu",
            "name": "organizer",
            "path": "static/app",
        },
    )

    log.info("Top menu script registration result: %s", script_result)

    # Register file action.
    file_action_result = _ocs(
        "POST",
        "/apps/app_api/api/v2/ui/files-actions-menu",
        json={
            "name": "ai_organize",
            "displayName": "AI Organize",
            "actionHandler": "file-action",
            "mime": app_action_mimes(),
            "order": 50,
        },
    )

    log.info("File action registration result: %s", file_action_result)

    log.info("AI Organizer UI registration completed")


def unregister_ui() -> None:
    log.info("Unregistering AI Organizer UI")

    try:
        _ocs(
            "DELETE",
            "/apps/app_api/api/v1/ui/files-actions-menu",
            json={"name": "ai_organize"},
            allow_404=True,
        )
    finally:
        try:
            _ocs(
                "DELETE",
                "/apps/app_api/api/v1/ui/script",
                json={
                    "type": "top_menu",
                    "name": "organizer",
                    "path": "static/app",
                },
                allow_404=True,
            )
        finally:
            try:
                _ocs(
                    "DELETE",
                    "/apps/app_api/api/v1/ui/style",
                    json={
                        "type": "top_menu",
                        "name": "organizer",
                        "path": "static/app",
                    },
                    allow_404=True,
                )
            finally:
                _ocs(
                    "DELETE",
                    "/apps/app_api/api/v1/ui/top-menu",
                    json={"name": "organizer"},
                    allow_404=True,
                )

    log.info("AI Organizer UI unregistered")


@app.get("/heartbeat")
def heartbeat():
    return {"status": "ok"}


@app.put("/enabled")
def enabled(request: Request, enabled: int = 1):
    try:
        log.info("Received enabled state: %s", enabled)
        _bind_appapi_user(request)

        app.state.scheduler.enabled = bool(int(enabled))
        if int(enabled):
            register_ui()
        else:
            unregister_ui()

        return {"error": ""}

    except Exception as exc:
        log.exception("Failed changing enabled state")

        return JSONResponse(
            status_code=500,
            content={"error": str(exc)}
        )


@app.get("/", response_class=HTMLResponse)
@app.get("/organize", response_class=HTMLResponse)
def ui(request: Request):
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Organizer</title>
</head>
<body>
  <main id="ai_organize" class="app-shell">
    <header class="app-header">
      <div>
        <h1>AI Organizer</h1>
        <p>Review AI suggestions before changing anything in Nextcloud.</p>
      </div>
      <button id="reanalyze" class="secondary" type="button" disabled>Re-analyze</button>
    </header>

    <section id="status" class="status-card">Choose <strong>AI Organize</strong> from a file's menu.</section>
    <section id="suggestion" class="suggestion-card hidden" aria-live="polite"></section>
  </main>
</body>
</html>"""
