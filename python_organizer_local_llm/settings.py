"""Persisted, validated admin settings. Deployment credentials remain outside SQLite."""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
import logging
import math
import os
from pathlib import Path
import re
import threading
from urllib.parse import urlsplit

from dotenv import load_dotenv

from python_organizer_local_llm.file_types import (
    extensions_for, from_extensions, validate_ids,
)


@dataclass(frozen=True)
class EnvironmentSettings:
    """Deployment/runtime settings sourced only from environment variables.

    User-editable organizer behavior remains in :class:`SettingsService` and
    SQLite. Secrets are deliberately excluded from that persisted settings
    payload.
    """

    app_id: str = "ai_organizer"
    app_version: str = "0.2.1"
    app_api_version: str = "4.0.0"
    app_secret: str = field(default="", repr=False)
    app_user: str = ""
    nextcloud_url: str = ""
    nextcloud_username: str = ""
    nextcloud_app_password: str = field(default="", repr=False)
    persistent_storage: str = ""
    config_file: str = ""
    log_level: str = "INFO"
    ollama_url: str = ""
    ollama_model: str = ""
    paperless_enabled: bool = False
    paperless_inbox: str = ""
    paperless_enabled_from_env: bool = False

    @property
    def debug_logging(self) -> bool:
        return self.log_level == "DEBUG"

    def require_exapp_url(self) -> str:
        """Return a validated Nextcloud URL for the FastAPI/AppAPI entry point."""
        if not self.nextcloud_url:
            raise RuntimeError(
                "NEXTCLOUD_URL is required. Set it in the container environment "
                "or local .env file. Example: http://192.168.1.2:8080"
            )
        _validate_http_origin("NEXTCLOUD_URL", self.nextcloud_url)
        return self.nextcloud_url


def _env(name: str, default: str = "") -> str:
    """Read and trim one environment variable. Keep all os.getenv calls here."""
    return str(os.getenv(name, default) or "").strip()


def _env_bool(name: str, default: bool = False) -> tuple[bool, bool]:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default, False
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True, True
    if value in {"0", "false", "no", "off"}:
        return False, True
    raise RuntimeError(
        f"{name} must be one of: true, false, 1, 0, yes, no, on, off"
    )


def _validate_http_origin(name: str, value: str) -> None:
    parts = urlsplit(value)
    if (parts.scheme not in {"http", "https"} or not parts.hostname
            or parts.username or parts.password or parts.query or parts.fragment):
        raise RuntimeError(
            f"{name} must be an http(s) URL without embedded credentials, "
            "query parameters, or fragments."
        )


def load_environment_settings(*, load_env_file: bool = True) -> EnvironmentSettings:
    """Load deployment settings. Real environment variables override ``.env``.

    ``python-dotenv`` is only a local-development convenience. Docker/AppAPI
    should inject the same variables at container start.
    """
    if load_env_file:
        load_dotenv(override=False)

    paperless_enabled, paperless_explicit = _env_bool("PAPERLESS_ENABLED", False)
    paperless_inbox = _env("INBOX_PATH")
    nextcloud_username = _env("NEXTCLOUD_USERNAME")
    nextcloud_url = _env("NEXTCLOUD_URL").rstrip("/")
    ollama_url = _env("OLLAMA_URL").rstrip("/")

    if nextcloud_url:
        _validate_http_origin("NEXTCLOUD_URL", nextcloud_url)
    if ollama_url:
        _validate_http_origin("OLLAMA_URL", ollama_url)

    return EnvironmentSettings(
        app_id=_env("APP_ID", "ai_organizer"),
        app_version=_env("APP_VERSION", "0.2.1"),
        app_api_version=_env("AA_VERSION", "4.0.0"),
        app_secret=_env("APP_SECRET"),
        app_user=_env("APP_USER", nextcloud_username),
        nextcloud_url=nextcloud_url,
        nextcloud_username=nextcloud_username,
        nextcloud_app_password=_env("NEXTCLOUD_APP_PASSWORD"),
        persistent_storage=_env("APP_PERSISTENT_STORAGE"),
        config_file=_env("AI_ORGANIZER_CONFIG"),
        log_level=_env("LOG_LEVEL", "INFO").upper(),
        ollama_url=ollama_url,
        ollama_model=_env("OLLAMA_MODEL"),
        paperless_enabled=paperless_enabled,
        paperless_inbox=paperless_inbox,
        paperless_enabled_from_env=paperless_explicit,
    )


class SettingsService:
    PAPERLESS_POLICY_KEYS = (
        'paperless_never_send',
        'paperless_prefer_send',
    )

    def __init__(self, organizer):
        self.organizer = organizer
        self.lock = threading.RLock()
        self.log = logging.getLogger("settings")

        stored = organizer.database.load_settings()
        first_seed = not bool(stored)
        self.defaults = self._defaults(stored)
        self.values = self.validate(self._merge_with_defaults(stored), allow_incomplete=True)

        # Persist effective settings on first initialization.  Previously the
        # service only wrote a row when it detected a non-empty Paperless-policy
        # migration.  A brand-new/empty organizer_settings table could therefore
        # remain empty even though the UI was using in-memory defaults.
        #
        # For existing databases, blank Paperless lists are still treated as
        # "use built-in defaults" and the effective values are written back.
        migrated = [
            key for key in self.PAPERLESS_POLICY_KEYS
            if not (stored or {}).get(key) and self.values.get(key)
        ]

        if first_seed or migrated:
            organizer.database.save_settings(self.values)
            persisted = organizer.database.load_settings()
            if persisted != self.values:
                raise RuntimeError(
                    "Organizer settings were written to SQLite but could not be "
                    "read back unchanged. Check the configured database path and "
                    "filesystem permissions."
                )

            if first_seed:
                self.log.info(
                    "Seeded organizer_settings from effective config/runtime defaults."
                )
            if migrated:
                self.log.info(
                    "Persisted built-in Paperless policy defaults: %s",
                    ", ".join(migrated),
                )

        database_path = Path(organizer.database.path).expanduser().resolve()
        config_paperless = organizer.classifier.config.get("paperless", {}) or {}
        self.log.info(
            "Settings initialized: config=%s database=%s saved_row=%s "
            "paperless_never_send=%s paperless_prefer_send=%s",
            getattr(organizer, "config_file", None) or "built-in defaults",
            database_path,
            bool(organizer.database.load_settings()),
            self.values.get("paperless_never_send", []),
            self.values.get("paperless_prefer_send", []),
        )
        self.log.info(
            "Paperless built-in policy: never_send=%s prefer_send=%s",
            config_paperless.get("never_send") or [],
            config_paperless.get("prefer_send") or [],
        )

        self._apply(self.values)

    def _merge_with_defaults(self, stored):
        """Merge persisted settings over config defaults.

        Paperless policy lists are slightly different from ordinary settings:
        an absent or empty persisted list means "use the built-in values".
        This prevents an old/blank SQLite value from silently disabling the
        administrator policy shipped with AI Organizer. A non-empty persisted list
        remains an explicit UI override.
        """
        merged = {**self.defaults, **(stored or {})}
        for key in self.PAPERLESS_POLICY_KEYS:
            saved = (stored or {}).get(key)
            if not saved:
                merged[key] = copy.deepcopy(self.defaults[key])
        return merged

    def _restore_blank_policy_defaults(self, values):
        """Treat a blank Paperless policy textarea as "restore built-in defaults"."""
        restored = copy.deepcopy(values)
        for key in self.PAPERLESS_POLICY_KEYS:
            if not restored.get(key):
                restored[key] = copy.deepcopy(self.defaults[key])
        return restored

    def _defaults(self, stored=None):
        stored = stored or {}
        config = self.organizer.classifier.config
        ollama = config.get('ollama', {})
        runtime = getattr(self.organizer, 'runtime_settings', None)
        if runtime is None:
            runtime = load_environment_settings(load_env_file=False)

        ollama_url = str(
            stored.get('ollama_url') or runtime.ollama_url or ollama.get('url') or ''
        ).strip().rstrip('/')
        model = str(
            stored.get('model') or runtime.ollama_model or ollama.get('model') or ''
        ).strip()
        classifier = config.get('classifier', {})
        ocr = config.get('ocr', {})
        scanner = self.organizer.scanner
        paperless_config = config.get('paperless', {})
        never_send = paperless_config.get('never_send') or []
        if isinstance(never_send, str):
            never_send = [never_send]
        prefer_send = paperless_config.get('prefer_send') or []
        if isinstance(prefer_send, str):
            prefer_send = [prefer_send]
        return {
            'ollama_url': ollama_url,
            'model': model,
            'timeout': int(ollama.get('timeout', 180)),
            'temperature': float(ollama.get('temperature', 0)),
            'max_content_chars': int(classifier.get('max_content_chars', 8000)),
            'ocr_enabled': bool(ocr.get('enabled', True)),
            'ocr_max_pages': int(ocr.get('max_pages', 10)),
            'file_types': from_extensions(config.get('scanner', {}).get('allowed_extensions')),
            'scan_paths': list(scanner.scan_paths),
            'exclude_paths': list(scanner.exclude_paths),
            'schedule_enabled': False,
            'interval_minutes': 60,
            'auto_analyze': False,
            'auto_apply': False,
            'auto_apply_warning_accepted': False,
            'minimum_auto_confidence': 0.95,
            'global_instructions': '',
            'folder_rules': [],
            'paperless_enabled': (runtime.paperless_enabled if runtime.paperless_enabled_from_env
                                  else bool(config.get('paperless', {}).get('enabled', False))),
            'paperless_inbox': str(
                stored.get('paperless_inbox')
                or runtime.paperless_inbox
                or config.get('paperless', {}).get('inbox_path', '/inbox')
            ),
            'paperless_never_send': list(never_send),
            'paperless_prefer_send': list(prefer_send),
        }

    @staticmethod
    def _paths(raw, name, allow_empty=False):
        if not isinstance(raw, list) or len(raw) > 100:
            raise ValueError(f'{name} must be a list of at most 100 folders')
        clean = []
        for value in raw:
            if not isinstance(value, str):
                raise ValueError(f'{name} must contain folder paths')
            text = '/' + '/'.join(part for part in value.strip().replace('\\', '/').split('/') if part)
            if any(part in {'.', '..'} for part in text.split('/')):
                raise ValueError(f'{name} contains an invalid folder')
            if len(text) > 1024:
                raise ValueError(f'{name} contains a path that is too long')
            if text not in clean:
                clean.append(text)
        if not clean and not allow_empty:
            raise ValueError(f'{name} must contain at least one folder')
        return clean

    @staticmethod
    def _inside(path, folder):
        return folder == '/' or path == folder or path.startswith(folder.rstrip('/') + '/')

    @classmethod
    def validate(cls, incoming, *, allow_incomplete=False):
        allowed = {
            'ollama_url', 'model', 'timeout', 'temperature', 'max_content_chars', 'scan_paths',
            'exclude_paths', 'schedule_enabled', 'interval_minutes', 'auto_analyze',
            'auto_apply', 'auto_apply_warning_accepted', 'minimum_auto_confidence',
            'global_instructions', 'folder_rules', 'paperless_enabled', 'paperless_inbox',
            'paperless_never_send', 'paperless_prefer_send', 'ocr_enabled', 'ocr_max_pages', 'file_types'
        }
        if not isinstance(incoming, dict) or set(incoming) != allowed:
            raise ValueError('Settings payload is missing keys or contains unknown settings')
        v = copy.deepcopy(incoming)
        v['file_types'] = validate_ids(v['file_types'])
        url = str(v['ollama_url']).strip().rstrip('/')
        if url:
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password or parts.path not in (
                    '', '/') or parts.query or parts.fragment:
                raise ValueError('Ollama URL must be an http(s) host and optional port, without credentials or path')
        elif not allow_incomplete:
            raise ValueError('Enter the Ollama server URL')
        v['ollama_url'] = url
        v['model'] = str(v['model']).strip()
        if v['model']:
            if len(v['model']) > 150 or any(c.isspace() for c in v['model']):
                raise ValueError('Select a valid Ollama model name')
        elif not allow_incomplete:
            raise ValueError('Select an Ollama model')
        for name, low, high in [('timeout', 5, 1800), ('max_content_chars', 500, 100000),
                                ('interval_minutes', 1, 10080), ('ocr_max_pages', 1, 100)]:
            if isinstance(v[name], bool) or not isinstance(v[name], int) or not low <= v[name] <= high:
                raise ValueError(f'{name} must be between {low} and {high}')
        temperature = v['temperature']
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature) or not 0 <= temperature <= 2):
            raise ValueError('Ollama temperature must be a finite number between 0 and 2')
        v['temperature'] = float(temperature)
        for name in ('schedule_enabled', 'auto_analyze', 'auto_apply', 'auto_apply_warning_accepted', 'ocr_enabled'):
            if not isinstance(v[name], bool):
                raise ValueError(f'{name} must be true or false')
        if float(v['minimum_auto_confidence']) != 0.95:
            raise ValueError('Automatic Apply requires at least 95% confidence')
        v['minimum_auto_confidence'] = 0.95
        if v['auto_apply'] and not v['auto_apply_warning_accepted']:
            raise ValueError('You must acknowledge the file-movement warning before enabling Automatic Apply')
        # Paperless category policy is administrator-controlled. Normalize both
        # lists when saved so matching is stable across restarts and model wording.
        def normalize_policy_list(raw, label):
            if not isinstance(raw, list) or len(raw) > 100:
                raise ValueError(f'{label} must be a list of at most 100 entries')
            normalized = []
            for category in raw:
                if not isinstance(category, str) or len(category) > 100:
                    raise ValueError(f'{label} must contain short text entries')
                entry = category.strip()
                if not re.fullmatch(r'[A-Za-z0-9]+(?:[ _-][A-Za-z0-9]+)*', entry):
                    raise ValueError(f'{label} may contain letters, numbers, spaces, _ and -')
                policy_name = entry.lower().replace('-', '_').replace(' ', '_')
                if policy_name and policy_name not in normalized:
                    normalized.append(policy_name)
            return normalized

        v['paperless_never_send'] = normalize_policy_list(
            v['paperless_never_send'], 'Paperless Always-Keep categories'
        )
        v['paperless_prefer_send'] = normalize_policy_list(
            v['paperless_prefer_send'], 'Paperless preferred categories'
        )
        if not isinstance(v['paperless_enabled'], bool):
            raise ValueError('paperless_enabled must be true or false')
        if not v['paperless_enabled'] and not str(v['paperless_inbox'] or '').strip():
            v['paperless_inbox'] = '/inbox'
        v['paperless_inbox'] = cls._paths([v['paperless_inbox']], 'Paperless consume folder')[0]
        if v['paperless_inbox'] == '/' or v['paperless_inbox'].casefold() == '/ai inbox':
            raise ValueError('Paperless consume folder must not be the Files root or AI Inbox')
        v['scan_paths'] = cls._paths(v['scan_paths'], 'scan_paths')
        if v['paperless_enabled'] and any(root != '/' and cls._inside(v['paperless_inbox'], root)
                                          for root in v['scan_paths']):
            raise ValueError('Paperless consume folder must not be inside a scan root')
        v['exclude_paths'] = cls._paths(v['exclude_paths'], 'exclude_paths', allow_empty=True)
        if '/' in v['exclude_paths']:
            raise ValueError('Cannot exclude the entire Nextcloud Files root')
        for scan in v['scan_paths']:
            if any(cls._inside(scan, excluded) for excluded in v['exclude_paths']):
                raise ValueError(f'Scan root {scan} is inside an excluded folder')
        instructions = v['global_instructions']
        if not isinstance(instructions, str) or len(instructions) > 8000:
            raise ValueError('Global instructions must be at most 8000 characters')
        rules = v['folder_rules']
        if not isinstance(rules, list) or len(rules) > 50:
            raise ValueError('folder_rules must be a list of at most 50 rules')
        result = []
        for rule in rules:
            if not isinstance(rule, dict) or set(rule) != {'folder', 'instructions'}:
                raise ValueError('Each folder rule requires folder and instructions')
            if not isinstance(rule['folder'], str) or not rule['folder'].strip():
                raise ValueError('Folder rule must have a nonempty folder path')
            folder = cls._paths([rule['folder']], 'folder rule')[0]
            instruction = rule['instructions']
            if not isinstance(instruction, str) or len(instruction) > 4000:
                raise ValueError('Folder rule must have at most 4000 characters')
            result.append({'folder': folder, 'instructions': instruction})
        v['folder_rules'] = result
        return v


    def is_configured(self):
        """Whether the minimum first-run Ollama settings have been completed."""
        with self.lock:
            return bool(self.values.get('ollama_url') and self.values.get('model'))

    def get(self):
        with self.lock:
            return copy.deepcopy(self.values)

    def save(self, values):
        with self.lock:
            # Empty Paperless policy lists mean "restore built-in defaults",
            # not "disable all policy". The effective defaults are persisted so
            # the Settings UI immediately reflects what the classifier is using.
            validated = self.validate(self._restore_blank_policy_defaults(values))
            self.organizer.database.save_settings(validated)
            self.values = validated
            self._apply(validated)
            return copy.deepcopy(validated)

    def _apply(self, v):
        classifier = self.organizer.classifier
        classifier.base_url = v['ollama_url']
        classifier.model = v['model']
        classifier.timeout = v['timeout']
        classifier.temperature = v['temperature']
        classifier.max_content_chars = v['max_content_chars']
        classifier.paperless_enabled = v['paperless_enabled']
        classifier.paperless_inbox = v['paperless_inbox']
        canonicalize_policy = getattr(classifier, '_canonical_policy_value', None)
        if not callable(canonicalize_policy):
            canonicalize_policy = lambda value: str(value).strip().lower().replace('-', '_').replace(' ', '_')
        classifier.paperless_never_send = {
            canonicalize_policy(value)
            for value in v['paperless_never_send']
        }
        classifier.paperless_prefer_send = {
            canonicalize_policy(value)
            for value in v['paperless_prefer_send']
        }
        self.organizer.paperless_enabled = v['paperless_enabled']
        self.organizer.paperless_inbox = v['paperless_inbox']
        classifier.global_instructions = v['global_instructions']
        classifier.folder_rules = copy.deepcopy(v['folder_rules'])
        scanner = self.organizer.scanner
        scanner.scan_paths = list(v['scan_paths'])
        scanner.allowed_extensions = extensions_for(v['file_types'])
        scanner.allow_sensitive = 'credential' in v['file_types']
        effective_excludes = list(v['exclude_paths'])
        if v['paperless_enabled'] and v['paperless_inbox'] not in effective_excludes:
            effective_excludes.append(v['paperless_inbox'])
        scanner.exclude_paths = effective_excludes
        nextcloud = self.organizer.nextcloud
        nextcloud.ocr_enabled = v['ocr_enabled']
        nextcloud.ocr_max_pages = v['ocr_max_pages']
        if hasattr(nextcloud, "_ocr_cache"):
            nextcloud._ocr_cache.clear()
        nextcloud.allowed_extensions = extensions_for(v['file_types'])
        nextcloud.allow_sensitive = 'credential' in v['file_types']
        nextcloud.scan_paths = list(v['scan_paths'])
        nextcloud.exclude_paths = list(effective_excludes)
