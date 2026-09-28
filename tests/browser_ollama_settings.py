"""Mocked Chromium regression for Ollama setup, Paperless conditional DOM and analysis gating."""
from pathlib import Path
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
script = (root / 'exapp/static/app.js').read_text()
style = (root / 'exapp/static/app.css').read_text()

mock = r'''
window.requests=[];
window.pullResult='completed';
window.settings={ollama_url:'',model:'',timeout:180,temperature:0,max_content_chars:8000,
 ocr_enabled:true,ocr_max_pages:10,file_types:['pdf'],scan_paths:['/AI Inbox'],exclude_paths:['/Photos'],
 schedule_enabled:false,interval_minutes:60,auto_analyze:false,auto_apply:false,
 auto_apply_warning_accepted:false,minimum_auto_confidence:.95,global_instructions:'',folder_rules:[],
 paperless_enabled:false,paperless_inbox:'/consume',paperless_never_send:['resume'],paperless_prefer_send:['invoice']};
window.catalog=[{id:'pdf',label:'PDF documents',extensions:['.pdf'],description:'PDF',mime_types:['application/pdf']}];
window.fetch=async(url,options={})=>{
 const path=url.split('/ai_organizer/').pop().split('?')[0];
 const body=options.body ? JSON.parse(options.body) : null;
 window.requests.push({path,method:options.method||'GET',body});
 if(path==='api/settings' && options.method==='PUT') {
   window.settings=body.settings;
   return {ok:true,status:200,json:async()=>({settings:window.settings,configured:!!(window.settings.ollama_url&&window.settings.model)})};
 }
 if(path==='api/settings')return {ok:true,status:200,json:async()=>({settings:window.settings,configured:!!(window.settings.ollama_url&&window.settings.model),automation:{},file_types_catalog:window.catalog})};
 if(path==='api/settings/ollama/test')return {ok:true,status:200,json:async()=>({connected:true,version:'0.12.0'})};
 if(path==='api/settings/ollama/models')return {ok:true,status:200,json:async()=>({connected:true,models:['qwen2.5:7b','llama3.1:8b']})};
 if(path==='api/settings/ollama/pull' && (options.method||'GET')==='POST')return {ok:true,status:202,json:async()=>({started:true,job_id:'pull-job',message:'Model download started: '+body.model+'.'})};
 if(path==='api/settings/ollama/pull/pull-job')return {ok:true,status:200,json:async()=> window.pullResult==='failed'
   ? ({job_id:'pull-job',status:'failed',model:'missing:model',message:'Model not found: missing:model. Check the model name and tag.',error:'Model not found: missing:model. Check the model name and tag.'})
   : ({job_id:'pull-job',status:'completed',model:body?.model||'',message:'Model download completed.'})};
 if(path==='api/dashboard/unprocessed')return {ok:true,status:200,json:async()=>({items:[{file_id:'42',name:'sample.pdf',path:'/AI Inbox/sample.pdf',status:'unprocessed'}],count:1})};
 if(path.startsWith('api/dashboard/'))return {ok:true,status:200,json:async()=>({items:[],count:0})};
 return {ok:false,status:404,json:async()=>({detail:'unknown '+path})};
};
'''

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True, executable_path='/usr/bin/chromium', args=['--no-sandbox'])
    page = browser.new_page(viewport={'width': 1450, 'height': 900})
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.evaluate('() => {' + mock + 'return true;}')
    page.set_content('<!doctype html><html><head><style>' + style + '</style></head>'
                     '<body><div id="content" class="app-app_api"></div><script>' + script + '</script></body></html>')

    page.get_by_text('Configure an Ollama URL and model in Settings before analysis can start.').wait_for()
    analyze = page.locator('button[data-file-index="0"]')
    assert analyze.is_disabled()

    # Use Advanced Settings rather than completing onboarding.
    page.locator('#onboarding-advanced').click()
    page.locator('#setting-url').fill('http://192.168.1.2:11434')
    page.locator('#settings-save').click()
    page.wait_for_function("window.requests.some(r => r.method==='PUT' && r.body && r.body.settings.ollama_url==='http://192.168.1.2:11434' && r.body.settings.model==='')")
    assert page.locator('#setting-model').input_value() == ''

    page.locator('#settings-ollama-test').click()
    page.get_by_text('Connected to Ollama 0.12.0.').wait_for()
    test_req = page.evaluate("window.requests.filter(r => r.path==='api/settings/ollama/test').pop()")
    assert test_req['body']['url'] == 'http://192.168.1.2:11434'

    page.locator('#settings-model-discover').click()
    page.get_by_text('2 local model(s) found. Choose one from the Model field or type a new model name.').wait_for()
    model_input = page.locator('#setting-model')
    assert model_input.get_attribute('list') == 'settings-model-options'
    options = page.locator('#settings-model-options option')
    assert options.count() == 2
    assert options.nth(0).get_attribute('value') == 'qwen2.5:7b'
    assert options.nth(1).get_attribute('value') == 'llama3.1:8b'
    model_input.fill('qwen2.5:7b')
    assert model_input.input_value() == 'qwen2.5:7b'

    # The same editable field also accepts a new registry model name.
    model_input.fill('CyberCrew/notmythos-8b:3.8-27b-uncensored')
    page.locator('#settings-model-pull').click()
    started = page.get_by_text('Model download started: CyberCrew/notmythos-8b:3.8-27b-uncensored.')
    started.wait_for()
    assert 'success' in page.locator('#settings-model-results').get_attribute('class')
    page.get_by_text('Model download completed.').wait_for(timeout=5000)

    # Failed pulls must become visible in the UI instead of only in server logs.
    page.evaluate("window.pullResult='failed'")
    model_input.fill('missing:model')
    page.locator('#settings-model-pull').click()
    page.get_by_text('Model not found: missing:model. Check the model name and tag.').wait_for(timeout=5000)
    assert 'error' in page.locator('#settings-model-results').get_attribute('class')

    # Continue with a valid model for the settings-save and analysis-gating checks.
    model_input.fill('qwen2.5:7b')

    # Paperless details are not merely hidden: they are removed from the DOM.
    page.locator('button[data-settings-tab="paperless"]').click()
    assert page.locator('#setting-paperless-inbox').count() == 0
    page.locator('#setting-paperless-enabled').check()
    assert page.locator('#setting-paperless-inbox').input_value() == '/consume'
    page.locator('#setting-paperless-inbox').fill('/new-consume')
    page.locator('#setting-paperless-never-send').fill('resume\nsource_code')
    page.locator('#setting-paperless-enabled').uncheck()
    assert page.locator('#setting-paperless-inbox').count() == 0

    # Save disabled Paperless: retained values must still be sent and persisted.
    page.locator('#settings-save').click()
    page.wait_for_function("window.settings.model==='qwen2.5:7b' && window.settings.paperless_enabled===false && window.settings.paperless_inbox==='/new-consume'")
    saved = page.evaluate('window.settings')
    assert saved['paperless_inbox'] == '/new-consume'
    assert saved['paperless_never_send'] == ['resume', 'source_code']

    page.locator('button[data-view="unprocessed"]').click()
    page.locator('button[data-file-index="0"]').wait_for()
    assert not page.locator('button[data-file-index="0"]').is_disabled()
    assert not errors, errors
    print('Browser settings: partial save, Ollama test/discovery, Paperless DOM removal/retention and Analyze gating: PASS')
    browser.close()
