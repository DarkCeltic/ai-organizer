"""Preferred Paperless categories, SQLite persistence, and deterministic precedence."""
from types import SimpleNamespace
import pytest
from python_organizer_local_llm.classifier import Classifier
from python_organizer_local_llm.database import Database
from python_organizer_local_llm.settings import SettingsService


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "test-model:latest")
    cfg = tmp_path / 'config.yaml'
    cfg.write_text('database:\n  path: "' + str(tmp_path / 'state.db') + '"\n'
                   'paperless:\n  enabled: true\n  inbox_path: /consume\n'
                   '  never_send: [resume, cv]\n  prefer_send: [receipt]\n')
    db = Database(str(cfg)); db.initialize()
    classifier = Classifier(str(cfg))
    cloud = SimpleNamespace(scan_paths=[], exclude_paths=[])
    scanner = SimpleNamespace(scan_paths=['/AI Inbox'], exclude_paths=[])
    org = SimpleNamespace(database=db, classifier=classifier, scanner=scanner,
                          nextcloud=cloud, paperless_enabled=True, paperless_inbox='/consume')
    return org, SettingsService(org)


def suggestion(category, candidate=False):
    return dict(category=category, paperless_candidate=candidate,
                suggested_folder='/Documents', suggested_filename='document.pdf',
                tags=['records'], confidence=.9, reason='Document content establishes category')


def test_paperless_policy_initial_yaml_defaults_and_reload(store):
    org, settings = store
    assert settings.get()['paperless_never_send'] == ['resume', 'cv']
    assert org.classifier.paperless_never_send == {'resume', 'cv'}
    assert settings.get()['paperless_prefer_send'] == ['receipt']
    assert org.classifier.paperless_prefer_send == {'receipt'}
    # Config policy defaults are not only effective in memory; startup persists
    # them so the Settings API/UI reads populated values from SQLite.
    persisted = org.database.load_settings()
    assert persisted['paperless_never_send'] == ['resume', 'cv']
    assert persisted['paperless_prefer_send'] == ['receipt']
    updated = settings.get(); updated['paperless_prefer_send'] = ['statement', ' tax ', 'statement']
    settings.save(updated)
    assert settings.get()['paperless_prefer_send'] == ['statement', 'tax']
    assert org.database.load_settings()['paperless_prefer_send'] == ['statement', 'tax']
    fresh = SettingsService(org)
    assert fresh.get()['paperless_never_send'] == ['resume', 'cv']
    assert fresh.get()['paperless_prefer_send'] == ['statement', 'tax']
    assert org.classifier.paperless_prefer_send == {'statement', 'tax'}


def test_prefer_send_classification_deterministic_no_automatic_move(store):
    org, settings = store
    s = org.classifier._apply_paperless_policy(suggestion('receipt'), 'receipt.pdf', 'itemized groceries')
    assert s['paperless_candidate'] is True
    assert s['suggested_folder'] == '/Documents' and s['tags'] == ['records']
    # A configured invoice category must be deterministically Paperless-eligible,
    # regardless of the model's original candidate value.
    org.classifier.paperless_prefer_send.add('invoice')
    invoice = org.classifier._apply_paperless_policy(
        suggestion('invoice', False), 'old_phone_bill.pdf', 'monthly phone bill'
    )
    assert invoice['paperless_candidate'] is True
    assert org.classifier._apply_paperless_policy(suggestion('notes'), 'notes.pdf', 'working notes')['paperless_candidate'] is False


def test_never_send_overrides_prefer_send(store):
    org, settings = store
    changes = settings.get(); changes['paperless_prefer_send'] = ['resume', 'receipt']
    settings.save(changes)
    record = org.classifier._apply_paperless_policy(suggestion('resume', True), 'my_resume.pdf', 'experience')
    assert record['paperless_candidate'] is False
    assert record['suggested_folder'] == '/Documents'
    changes = settings.get(); changes['paperless_enabled'] = False; settings.save(changes)
    assert org.classifier._apply_paperless_policy(suggestion('receipt'), 'receipt.pdf', 'itemized')['paperless_candidate'] is False



def test_blank_policy_lists_restore_config_defaults(store):
    org, settings = store
    changes = settings.get()
    changes['paperless_never_send'] = []
    changes['paperless_prefer_send'] = []
    saved = settings.save(changes)

    assert saved['paperless_never_send'] == ['resume', 'cv']
    assert saved['paperless_prefer_send'] == ['receipt']
    assert org.database.load_settings()['paperless_never_send'] == ['resume', 'cv']
    assert org.database.load_settings()['paperless_prefer_send'] == ['receipt']
    assert org.classifier.paperless_never_send == {'resume', 'cv'}
    assert org.classifier.paperless_prefer_send == {'receipt'}


def test_nonempty_policy_lists_remain_user_overrides(store):
    org, settings = store
    changes = settings.get()
    changes['paperless_never_send'] = ['source_code']
    changes['paperless_prefer_send'] = ['resume']
    settings.save(changes)

    record = org.classifier._apply_paperless_policy(
        suggestion('resume', False), 'my_resume.pdf', 'experience and employment history'
    )
    assert record['paperless_candidate'] is True
    assert settings.get()['paperless_never_send'] == ['source_code']
    assert settings.get()['paperless_prefer_send'] == ['resume']


def test_prefer_send_validation_and_legacy_sqlite_settings(store):
    org, settings = store
    v = settings.get(); v['paperless_prefer_send'] = ['receipt; drop table suggestions']
    with pytest.raises(ValueError, match='preferred categories'):
        settings.save(v)

    # Missing keys from an older release inherit config.yaml defaults.
    old = settings.get(); old.pop('paperless_prefer_send'); old.pop('paperless_never_send')
    org.database.save_settings(old)
    restored = SettingsService(org)
    assert restored.get()['paperless_never_send'] == ['resume', 'cv']
    assert restored.get()['paperless_prefer_send'] == ['receipt']

    # Explicitly persisted blank lists also mean "use config.yaml defaults".
    blank = restored.get()
    blank['paperless_never_send'] = []
    blank['paperless_prefer_send'] = []
    org.database.save_settings(blank)
    restored_blank = SettingsService(org)
    assert restored_blank.get()['paperless_never_send'] == ['resume', 'cv']
    assert restored_blank.get()['paperless_prefer_send'] == ['receipt']
    migrated = org.database.load_settings()
    assert migrated['paperless_never_send'] == ['resume', 'cv']
    assert migrated['paperless_prefer_send'] == ['receipt']


def test_prompt_describes_admin_policy_and_rule_priority(store):
    org, settings = store
    org.classifier.global_instructions = 'Docker Compose files must use /docker-compose/<application>.'
    prompt = org.classifier._build_prompt('receipt.pdf', '/AI Inbox/receipt.pdf', 'Grocery itemized', [], [], False)
    assert '=== ADMINISTRATOR PAPERLESS POLICY ===' in prompt
    assert 'ALWAYS KEEP IN NEXTCLOUD categories' in prompt
    assert 'PREFER PAPERLESS categories' in prompt
    assert 'ALWAYS KEEP IN NEXTCLOUD takes precedence' in prompt
    assert 'ADMINISTRATOR ORGANIZATION RULES are supplied in the SYSTEM message' in prompt
    assert 'take priority over conflicting generic organization defaults' in prompt
    assert 'Docker Compose files must use /docker-compose/<application>.' not in prompt
    assert '* receipt' in prompt


def test_admin_organization_rules_are_promoted_to_system_message(store, monkeypatch):
    org, _settings = store
    rule = (
        'For Docker Compose files use '
        '/docker-compose/<application>/docker-compose.yml exactly.'
    )
    org.classifier.global_instructions = rule
    prompt = org.classifier._build_prompt(
        'docker-compose.yml',
        '/AI Inbox/docker-compose.yml',
        'services:\n  immich-server:\n    image: ghcr.io/immich-app/immich-server:release',
        [],
        [],
        False,
    )

    captured = {}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                'message': {
                    'content': (
                        '{"suggested_filename":"docker-compose.yml",'
                        '"suggested_folder":"/docker-compose/immich",'
                        '"tags":["docker","immich"],'
                        '"category":"docker_compose",'
                        '"paperless_candidate":false,'
                        '"confidence":0.99,'
                        '"reason":"Immich compose stack."}'
                    )
                }
            }

    def fake_post(url, json, timeout):
        captured['payload'] = json
        return FakeResponse()

    monkeypatch.setattr('python_organizer_local_llm.classifier.requests.post', fake_post)
    org.classifier._query_ollama(prompt, organization_rules=rule)

    system_message = captured['payload']['messages'][0]['content']
    assert rule in system_message
    assert 'authoritative for filename, folder structure' in system_message
    assert 'resolve the placeholder' in system_message
    assert 'do not add descriptive prefixes' in system_message
    assert 'return the full path' in system_message
    assert 'FINAL COMPLIANCE CHECK' in prompt
    assert rule not in prompt
    schema = captured['payload']['format']
    assert isinstance(schema, dict)
    assert set(schema['required']) == {
        'suggested_filename', 'suggested_folder', 'tags', 'category',
        'paperless_candidate', 'confidence', 'reason'
    }
    assert schema['additionalProperties'] is False
    assert captured['payload']['options']['num_predict'] == 512
