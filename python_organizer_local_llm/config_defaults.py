"""Built-in AI Organizer behavior defaults.

Production/App Store installs do not require a config.yaml file.  A YAML file may
still be supplied explicitly for local development or legacy deployments; when
present, it is deep-merged over these defaults.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

import yaml


DEFAULT_CONFIG: Dict[str, Any] = {
    "nextcloud": {
        "verify_ssl": True,
        "timeout": 60,
        "folder_tree_paths": ["/"],
    },
    "scanner": {
        "scan_paths": ["/AI Inbox"],
        "exclude_paths": [
            "/paperless-media",
            "/inbox",
            "/Photos",
            "/AI Ignored",
        ],
        "allowed_extensions": [
            "pdf",
            "txt",
            "md",
            "docx",
            "odt",
            "rtf",
            "log",
            "csv",
            "json",
            "xml",
            "xlsx",
        ],
    },
    "ollama": {
        "timeout": 180,
        "temperature": 0,
        "num_predict": 512,
    },
    "classifier": {
        "max_content_chars": 8000,
        "max_folder_entries": 500,
        "max_tag_entries": 200,
        "minimum_confidence": 0.70,
    },
    "ocr": {
        "enabled": True,
        "max_pages": 10,
    },
    "paperless": {
        "enabled": False,
        "inbox_path": "/inbox",
        "never_send": [
            "resume",
            "cv",
            "curriculum vitae",
            "cover letter",
            "portfolio",
            "source_code",
            "project",
            "template",
        ],
        "prefer_send": [
            "receipt",
            "invoice",
            "statement",
            "tax",
            "insurance",
            "contract",
            "warranty",
        ],
    },
    "database": {
        "path": "data/ai_organizer.db",
    },
}


def _deep_merge(base: Dict[str, Any], override: Mapping[str, Any]) -> Dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def load_config(config_file: Optional[str] = None) -> Dict[str, Any]:
    """Return built-in defaults, optionally merged with an explicit YAML file.

    No file is required.  If a caller explicitly names a configuration file,
    however, a missing or invalid file remains an error so typos do not silently
    change local/legacy deployment behavior.
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    if not config_file:
        return config

    path = Path(config_file).expanduser().resolve()
    if not path.is_file():
        raise RuntimeError(f"Configuration file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as handle:
            override = yaml.safe_load(handle) or {}
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML configuration: {path}") from exc

    if not isinstance(override, dict):
        raise RuntimeError("Configuration root must be a YAML mapping.")
    return _deep_merge(config, override)


def config_base_dir(config_file: Optional[str] = None) -> Path:
    """Base directory for relative legacy paths when no YAML file exists."""
    if config_file:
        return Path(config_file).expanduser().resolve().parent
    return Path.cwd().resolve()
