"""Release-packaging regressions for AppAPI/HaRP deployment."""
from pathlib import Path
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]


def test_release_identity_is_consistent():
    tree = ET.parse(ROOT / "appinfo" / "info.xml")
    assert tree.findtext("id") == "ai_organizer"
    assert tree.findtext("version") == "0.1.2"
    assert tree.findtext(".//docker-install/image") == "darthdragon/ai-organizer"
    assert tree.findtext(".//docker-install/image-tag") == "0.1.2"


def test_no_legacy_project_identifier_remains():
    legacy = (
        "ai-" + "nextcloud-organizer",
        "ai_" + "nextcloud_organizer",
        "nextcloud-" + "ai-organizer",
    )
    offenders = []
    for path in ROOT.rglob("*"):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        try:
            text = path.read_text(encoding="utf-8").lower()
        except UnicodeDecodeError:
            continue
        if any(value in text for value in legacy):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_harp_runtime_files_are_present_and_wired():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    start = (ROOT / "start.sh").read_text(encoding="utf-8")
    run = (ROOT / "run.sh").read_text(encoding="utf-8")
    health = (ROOT / "healthcheck.sh").read_text(encoding="utf-8")

    assert "frpc" in dockerfile
    assert 'ENTRYPOINT ["/start.sh", "/run.sh"]' in dockerfile
    assert 'unixPath = "/tmp/exapp.sock"' in start
    assert '--uds "$SOCKET"' in run
    assert '--unix-socket /tmp/exapp.sock' in health


def test_managed_persistence_uses_appapi_volume(tmp_path, monkeypatch):
    from python_organizer_local_llm.settings import load_environment_settings

    monkeypatch.setenv("APP_PERSISTENT_STORAGE", str(tmp_path))
    env = load_environment_settings(load_env_file=False)
    assert env.persistent_storage == str(tmp_path)
