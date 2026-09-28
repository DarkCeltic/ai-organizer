"""Headless Chromium regression test for first-run onboarding."""
import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_SETTINGS = {
    'ollama_url': '',
    'model': '',
    'request_timeout': 180,
    'max_content_chars': 8000,
    'temperature': 0,
    'num_predict': 512,
    'scan_paths': ['/AI Inbox'],
    'exclude_paths': ['/paperless-media', '/inbox', '/Photos', '/AI Ignored'],
    'allowed_extensions': ['pdf', 'txt', 'md', 'docx', 'odt', 'rtf', 'log', 'csv', 'json', 'xml', 'xlsx'],
    'paperless_enabled': False,
    'paperless_inbox': '/inbox',
    'never_send': ['resume', 'cv', 'curriculum_vitae', 'cover_letter', 'portfolio', 'source_code', 'project', 'template'],
    'prefer_send': ['receipt', 'invoice', 'statement', 'tax', 'insurance', 'contract', 'warranty'],
    'global_instructions': '',
    'folder_rules': [],
    'ocr_enabled': True,
    'ocr_max_pages': 10,
    'scan_interval_seconds': 300,
    'auto_process': False,
    'auto_apply': False,
    'auto_apply_confidence': 0.95,
    'auto_apply_warning_accepted': False,
}


def main():
    writes = []
    errors = []

    def fake_api(route):
        suffix = route.request.url.split('/ai_organizer/')[-1].split('?')[0]
        if suffix in {'api/dashboard/unprocessed', 'api/dashboard/review', 'api/dashboard/failed', 'api/dashboard/history'}:
            route.fulfill(status=200, content_type='application/json', body=json.dumps({'items': [], 'count': 0}))
            return
        if suffix == 'api/settings' and route.request.method == 'GET':
            route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'configured': False,
                'auth_mode': 'appapi',
                'settings': DEFAULT_SETTINGS,
                'automation': {},
                'file_types_catalog': [],
            }))
            return
        if suffix == 'api/settings' and route.request.method == 'PUT':
            payload = route.request.post_data_json
            writes.append(payload)
            route.fulfill(status=200, content_type='application/json', body=json.dumps({
                'configured': True,
                'settings': payload['settings'],
            }))
            return
        route.fulfill(status=404, content_type='application/json', body=json.dumps({'detail': suffix}))

    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=shutil.which('chromium') or p.chromium.executable_path,
            headless=True,
            args=['--no-sandbox'],
        )
        page = browser.new_page(viewport={'width': 1280, 'height': 800})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.route('**/apps/app_api/proxy/ai_organizer/**', fake_api)
        page.set_content('''<html><head><base href="http://127.0.0.1:18080/"></head><body>
          <div id="content" class="app-app_api">
          <main id="ai_organize" class="app-shell"><header class="app-header">
          <div><h1>AI Organizer</h1></div><button id="reanalyze" disabled>Re-analyze</button>
          </header><section id="status">Loading</section>
          <section id="suggestion" class="suggestion-card hidden"></section></main>
          </div></body></html>''')
        page.add_style_tag(path=str(ROOT / 'exapp/static/app.css'))
        page.add_script_tag(path=str(ROOT / 'exapp/static/app.js'))

        dialog = page.locator('.organizer-onboarding')
        page.get_by_text('Connect AI Organizer to Ollama').wait_for()
        assert not dialog.evaluate('(el) => el.classList.contains("hidden")')
        assert page.locator('#onboarding-paperless-inbox').input_value() == '/inbox'
        assert page.locator('#onboarding-ollama-url').input_value() == ''
        assert page.locator('#onboarding-ollama-model').input_value() == ''

        page.locator('#onboarding-save').click()
        page.get_by_text('Ollama URL and model are required.').wait_for()
        assert writes == []

        page.locator('#onboarding-ollama-url').fill('http://192.168.1.2:11434')
        page.locator('#onboarding-ollama-model').fill('qwen2.5:7b')
        page.locator('#onboarding-paperless').check()
        page.locator('#onboarding-save').click()
        page.get_by_text('AI Organizer setup saved.').wait_for()
        assert dialog.evaluate('(el) => el.classList.contains("hidden")')
        assert len(writes) == 1
        saved = writes[0]['settings']
        assert saved['ollama_url'] == 'http://192.168.1.2:11434'
        assert saved['model'] == 'qwen2.5:7b'
        assert saved['paperless_enabled'] is True
        assert saved['allowed_extensions'] == DEFAULT_SETTINGS['allowed_extensions']
        assert saved['never_send'] == DEFAULT_SETTINGS['never_send']
        assert saved['prefer_send'] == DEFAULT_SETTINGS['prefer_send']
        assert errors == [], errors
        browser.close()

    print('Browser onboarding: blank first-run settings, validation, defaults preservation and save: PASS')


if __name__ == '__main__':
    main()
