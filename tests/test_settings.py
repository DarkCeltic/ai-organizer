"""Offline regression tests: no live Ollama or Nextcloud requests."""
import json
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from python_organizer_local_llm.database import Database
from python_organizer_local_llm.settings import SettingsService, load_environment_settings
from exapp.automation import AutomationScheduler


@pytest.fixture
def system(tmp_path):
    config = tmp_path / 'config.yaml'
    config.write_text('database:\n  path: "' + str(tmp_path / 'real.db') + '"\n')
    db = Database(str(config))
    db.initialize()
    classifier = SimpleNamespace(config={
        'ollama': {'url': 'http://localhost:11434', 'model': 'qwen2.5:7b', 'timeout': 120},
        'classifier': {'max_content_chars': 8000}
    }, base_url='', model='', timeout=0, max_content_chars=0)
    scanner = SimpleNamespace(scan_paths=['/AI Inbox'], exclude_paths=['/Photos'],
                              is_excluded=lambda path: path.startswith('/Photos'))
    nextcloud = SimpleNamespace(scan_paths=[], exclude_paths=[])
    organizer = SimpleNamespace(database=db, classifier=classifier,
                                scanner=scanner, nextcloud=nextcloud)
    return SimpleNamespace(db=db, organizer=organizer, service=SettingsService(organizer))


def test_persist_and_reload(system):
    v = system.service.get()
    v.update(ollama_url='http://192.168.1.2:11434', model='local-model:7b', timeout=300,
             scan_paths=['/AI Inbox', '/Work'], exclude_paths=['/Photos', '/Work/Private'],
             interval_minutes=15, schedule_enabled=True, auto_analyze=True,
             global_instructions='Prefer current folders.',
             folder_rules=[{'folder': '/Work', 'instructions': 'Use /Work/Reports when suitable.'}])
    system.service.save(v)
    restored = SettingsService(system.organizer)
    assert restored.get() == v
    assert system.organizer.classifier.base_url == 'http://192.168.1.2:11434'
    assert system.organizer.scanner.scan_paths == ['/AI Inbox', '/Work']
    assert system.organizer.nextcloud.exclude_paths == ['/Photos', '/Work/Private']


def test_auto_apply_defaults_off_and_requires_warning(system):
    v = system.service.get()
    assert v['auto_apply'] is False and v['minimum_auto_confidence'] == 0.95
    v['auto_apply'] = True
    with pytest.raises(ValueError, match='warning'):
        system.service.save(v)
    v['auto_apply_warning_accepted'] = True
    system.service.save(v)
    assert system.service.get()['auto_apply'] is True
    v['minimum_auto_confidence'] = 0.80
    with pytest.raises(ValueError, match='95%'):
        system.service.save(v)


def test_scan_folder_validation(system):
    v = system.service.get()
    v['scan_paths'] = ['/Photos/Trips']
    with pytest.raises(ValueError, match='excluded'):
        system.service.save(v)
    v['scan_paths'] = ['/AI Inbox']
    v['exclude_paths'] = ['/']
    with pytest.raises(ValueError, match='entire'):
        system.service.save(v)
    v['exclude_paths'] = ['/Photos']
    v['folder_rules'] = [{'folder': '', 'instructions': 'oops'}]
    with pytest.raises(ValueError, match='nonempty'):
        system.service.save(v)


def test_settings_do_not_modify_history_or_suggestions(system):
    f = system.db.upsert_file('1001', '/AI Inbox/test.txt', 'etag', 'text/plain')
    s = system.db.save_suggestion(f, 'new.txt', '/Documents', ['work'], 'notes', False, .98, 'note')
    system.service.save(system.service.get())
    row = system.db.get_suggestion(s)
    assert row['original_path'] == '/AI Inbox/test.txt'
    assert row['status'] == 'pending'
    assert system.db.get_file_by_id(f)['path'] == '/AI Inbox/test.txt'


def test_run_metadata_persistence(system):
    system.db.start_automation_run()
    system.db.finish_automation_run(2, 1, 0)
    row = system.db.get_automation_run()
    assert row['analyzed'] == 2 and row['auto_applied'] == 1 and row['finished_at']


def test_auto_apply_skips_paperless_low_confidence_and_partial(system, monkeypatch):
    import exapp.automation as mod
    db = system.db
    for ix, (confidence, paperless) in enumerate(((.99, False), (.94, False), (.99, True)), start=1):
        f = db.upsert_file(str(ix), f'/AI Inbox/{ix}.txt', str(ix), 'text/plain')
        db.save_suggestion(f, f'{ix}.txt', '/Documents', [], 'notes', paperless, confidence, 'test')
    calls = []
    monkeypatch.setattr(mod, 'apply_suggestion', lambda file_id, body, request: calls.append(file_id))
    monkeypatch.setattr(mod, 'list_unprocessed', lambda **kw: {'items': []})
    v = system.service.get()
    v.update(auto_apply=True, auto_apply_warning_accepted=True, schedule_enabled=True)
    system.service.save(v)
    app = SimpleNamespace(state=SimpleNamespace(organizer=system.organizer, settings=system.service))
    scheduler = AutomationScheduler(app)
    scheduler.run_once()
    assert calls == ['1']
    assert db.get_automation_run()['auto_applied'] == 1


def test_info_xml_dynamic_routes_are_admin_only_for_single_database_architecture():
    import re
    tree = ET.parse(Path(__file__).resolve().parents[1] / 'appinfo/info.xml')
    routes = [(r.findtext('url'), r.findtext('access_level')) for r in tree.findall('.//routes/route')]
    assert routes

    # Frontend CSS/JS is intentionally public so Nextcloud's AppAPI asset loader
    # can fetch registered top-menu resources. No user data is served there.
    static_routes = [(url, level) for url, level in routes if url and url.startswith('^/static/')]
    assert static_routes == [('^/static/.*$', 'PUBLIC')]

    dynamic_routes = [(url, level) for url, level in routes if not (url and url.startswith('^/static/'))]
    assert dynamic_routes and all(level == 'ADMIN' for _, level in dynamic_routes)
    assert any(re.search(regex, '/api/settings') for regex, _ in routes)
    assert any(re.search(regex, '/api/dashboard/review') for regex, _ in routes)


def test_compose_and_config_contain_no_embedded_secret():
    root = Path(__file__).resolve().parents[1]
    import yaml
    compose = yaml.safe_load((root / 'compose.yaml').read_text())
    assert compose['services']['ai-organizer']['volumes'][0] == 'ai_organizer_data:/app/data'
    assert compose['services']['ai-organizer']['image'] == '${AI_ORGANIZER_IMAGE:-darthdragon/ai-organizer:latest}'
    dockerfile = (root / 'Dockerfile').read_text()
    assert 'COPY config.example.yaml /app/config.yaml' not in dockerfile
    assert not (root / 'config.example.yaml').exists()
    dockerignore = (root / '.dockerignore').read_text()
    assert 'config.yaml' in dockerignore and '.env' in dockerignore
    from python_organizer_local_llm.config_defaults import DEFAULT_CONFIG
    config = DEFAULT_CONFIG
    assert not {'url', 'username', 'app_password', 'password'} & set(config['nextcloud'])
    assert 'url' not in config['ollama'] and 'model' not in config['ollama']
    assert config['paperless']['enabled'] is False
    assert config['database']['path'] == 'data/ai_organizer.db'


def test_environment_settings_have_no_ollama_model_default(monkeypatch):
    for name in (
        'OLLAMA_URL', 'OLLAMA_MODEL', 'PAPERLESS_ENABLED', 'NEXTCLOUD_URL',
        'NEXTCLOUD_USERNAME', 'NEXTCLOUD_APP_PASSWORD', 'APP_PERSISTENT_STORAGE',
    ):
        monkeypatch.delenv(name, raising=False)
    env = load_environment_settings(load_env_file=False)
    assert env.ollama_url == ''
    assert env.ollama_model == ''
    assert env.paperless_enabled is False
    assert env.paperless_enabled_from_env is False


def test_environment_settings_parse_runtime_values(monkeypatch):
    monkeypatch.setenv('OLLAMA_URL', 'http://192.168.1.2:11434/')
    monkeypatch.setenv('OLLAMA_MODEL', 'local-model:7b')
    monkeypatch.setenv('PAPERLESS_ENABLED', 'yes')
    monkeypatch.setenv('NEXTCLOUD_URL', 'http://192.168.1.3:8080/')
    monkeypatch.setenv('NEXTCLOUD_USERNAME', 'tester')
    monkeypatch.setenv('NEXTCLOUD_APP_PASSWORD', 'secret-value')
    monkeypatch.setenv('APP_PERSISTENT_STORAGE', '/nc_app_ai_organizer_data')
    env = load_environment_settings(load_env_file=False)
    assert env.ollama_url == 'http://192.168.1.2:11434'
    assert env.ollama_model == 'local-model:7b'
    assert env.paperless_enabled is True and env.paperless_enabled_from_env is True
    assert env.nextcloud_url == 'http://192.168.1.3:8080'
    assert env.nextcloud_username == 'tester'
    assert env.app_user == 'tester'
    assert env.persistent_storage == '/nc_app_ai_organizer_data'
    assert 'secret-value' not in repr(env)


def test_environment_reads_are_centralized_in_settings_module():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for folder in ('exapp', 'python_organizer_local_llm'):
        for path in (root / folder).rglob('*.py'):
            if path.name == 'settings.py' and folder == 'python_organizer_local_llm':
                continue
            source = path.read_text(encoding='utf-8')
            if 'os.getenv(' in source or 'os.environ[' in source:
                offenders.append(str(path.relative_to(root)))
    assert offenders == []


def test_organizer_prefers_appapi_persistent_storage(tmp_path, monkeypatch):
    """Managed deployments must not keep SQLite in the replaceable image layer."""
    from python_organizer_local_llm.organizer import Organizer
    from python_organizer_local_llm.settings import EnvironmentSettings
    import python_organizer_local_llm.organizer as organizer_module

    config = tmp_path / 'config.yaml'
    config.write_text('database:\n  path: "/app/data/fallback.db"\n')
    persistent = tmp_path / 'managed-data'

    class DummyNextcloud:
        def __init__(self, *args, **kwargs):
            self.scan_paths = ['/AI Inbox']
            self.exclude_paths = []

    class DummyClassifier:
        def __init__(self, *args, **kwargs):
            self.config = {'paperless': {}}
            self.paperless_enabled = False

    class DummyScanner:
        def __init__(self, **kwargs):
            self.scan_paths = ['/AI Inbox']
            self.exclude_paths = []

    monkeypatch.setattr(organizer_module, 'NextcloudClient', DummyNextcloud)
    monkeypatch.setattr(organizer_module, 'Classifier', DummyClassifier)
    monkeypatch.setattr(organizer_module, 'Scanner', DummyScanner)

    runtime = EnvironmentSettings(persistent_storage=str(persistent))
    organizer = Organizer(str(config), runtime_settings=runtime)
    assert organizer.database.path == persistent / 'ai_organizer.db'


def test_first_initialization_seeds_full_settings_row(tmp_path):
    config = tmp_path / 'config.yaml'
    db_path = tmp_path / 'seed.db'
    config.write_text(
        'ollama:\n'
        '  url: "http://localhost:11434"\n'
        '  model: "qwen2.5:7b"\n'
        'paperless:\n'
        '  never_send:\n'
        '    - resume\n'
        '    - source_code\n'
        '  prefer_send:\n'
        '    - invoice\n'
        '    - receipt\n'
        'database:\n'
        f'  path: "{db_path}"\n',
        encoding='utf-8',
    )
    db = Database(str(config))
    db.initialize()
    assert db.load_settings() == {}

    classifier = SimpleNamespace(
        config={
            'ollama': {'url': 'http://localhost:11434', 'model': 'qwen2.5:7b'},
            'classifier': {'max_content_chars': 8000},
            'paperless': {
                'never_send': ['resume', 'source_code'],
                'prefer_send': ['invoice', 'receipt'],
            },
        },
        base_url='', model='', timeout=0, temperature=0.0,
        max_content_chars=0, paperless_enabled=False,
    )
    scanner = SimpleNamespace(
        scan_paths=['/AI Inbox'], exclude_paths=[], allowed_extensions=set(),
    )
    nextcloud = SimpleNamespace(scan_paths=[], exclude_paths=[])
    organizer = SimpleNamespace(
        database=db, classifier=classifier, scanner=scanner, nextcloud=nextcloud,
        runtime_settings=SimpleNamespace(
            config_file=str(config), ollama_url='', ollama_model='',
            paperless_enabled=False, paperless_enabled_from_env=False,
            paperless_inbox='',
        ),
    )

    service = SettingsService(organizer)
    saved = db.load_settings()
    assert saved
    assert saved == service.get()
    assert saved['paperless_never_send'] == ['resume', 'source_code']
    assert saved['paperless_prefer_send'] == ['invoice', 'receipt']

    with db._connect() as conn:
        count = conn.execute('SELECT COUNT(*) FROM organizer_settings').fetchone()[0]
    assert count == 1


def test_blank_saved_paperless_lists_are_persisted_from_config(tmp_path):
    config = tmp_path / 'config.yaml'
    db_path = tmp_path / 'migrate.db'
    config.write_text(
        'database:\n'
        f'  path: "{db_path}"\n',
        encoding='utf-8',
    )
    db = Database(str(config))
    db.initialize()

    classifier = SimpleNamespace(
        config={
            'ollama': {'url': 'http://localhost:11434', 'model': 'qwen2.5:7b'},
            'classifier': {'max_content_chars': 8000},
            'paperless': {
                'never_send': ['resume'],
                'prefer_send': ['invoice'],
            },
        },
        base_url='', model='', timeout=0, temperature=0.0,
        max_content_chars=0, paperless_enabled=False,
    )
    scanner = SimpleNamespace(
        scan_paths=['/AI Inbox'], exclude_paths=[], allowed_extensions=set(),
    )
    nextcloud = SimpleNamespace(scan_paths=[], exclude_paths=[])
    organizer = SimpleNamespace(
        database=db, classifier=classifier, scanner=scanner, nextcloud=nextcloud,
        runtime_settings=SimpleNamespace(
            config_file=str(config), ollama_url='', ollama_model='',
            paperless_enabled=False, paperless_enabled_from_env=False,
            paperless_inbox='',
        ),
    )

    # Seed once, then deliberately persist blank lists to emulate an older build.
    service = SettingsService(organizer)
    old = service.get()
    old['paperless_never_send'] = []
    old['paperless_prefer_send'] = []
    db.save_settings(old)

    migrated = SettingsService(organizer)
    saved = db.load_settings()
    assert migrated.get()['paperless_never_send'] == ['resume']
    assert migrated.get()['paperless_prefer_send'] == ['invoice']
    assert saved['paperless_never_send'] == ['resume']
    assert saved['paperless_prefer_send'] == ['invoice']


def test_database_relative_path_is_anchored_to_config_directory(tmp_path):
    config_dir = tmp_path / 'nested' / 'project'
    config_dir.mkdir(parents=True)
    config = config_dir / 'config.yaml'
    config.write_text('database:\n  path: "data/settings.db"\n', encoding='utf-8')

    db = Database(str(config))

    assert db.config_file == config.resolve()
    assert db.path == (config_dir / 'data' / 'settings.db').resolve()


def test_organizer_resolves_one_config_path_for_all_components(tmp_path, monkeypatch):
    from python_organizer_local_llm.organizer import Organizer
    from python_organizer_local_llm.settings import EnvironmentSettings
    import python_organizer_local_llm.organizer as organizer_module

    config = tmp_path / 'config.yaml'
    config.write_text(
        'database:\n  path: "data/settings.db"\n'
        'paperless:\n  never_send: [resume]\n  prefer_send: [invoice]\n',
        encoding='utf-8',
    )
    received = {}

    class DummyNextcloud:
        def __init__(self, config_file, **kwargs):
            received['nextcloud'] = config_file
            self.scan_paths = ['/AI Inbox']
            self.exclude_paths = []

    class DummyClassifier:
        def __init__(self, config_file, **kwargs):
            received['classifier'] = config_file
            self.config = {
                'paperless': {'never_send': ['resume'], 'prefer_send': ['invoice']}
            }
            self.paperless_enabled = False

    class DummyScanner:
        def __init__(self, *, config_file, **kwargs):
            received['scanner'] = config_file
            self.scan_paths = ['/AI Inbox']
            self.exclude_paths = []

    monkeypatch.setattr(organizer_module, 'NextcloudClient', DummyNextcloud)
    monkeypatch.setattr(organizer_module, 'Classifier', DummyClassifier)
    monkeypatch.setattr(organizer_module, 'Scanner', DummyScanner)

    organizer = Organizer(str(config), runtime_settings=EnvironmentSettings())
    expected = str(config.resolve())
    assert organizer.config_file == expected
    assert received == {
        'nextcloud': expected,
        'classifier': expected,
        'scanner': expected,
    }
    assert organizer.database.path == (tmp_path / 'data' / 'settings.db').resolve()
