import json

from python_organizer_local_llm.classifier import Classifier


def make_classifier(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(
        """
ollama:
  url: http://localhost:11434
  model: qwen2.5:7b
  temperature: 0
  num_predict: 512
classifier:
  max_content_chars: 8000
  max_folder_entries: 500
  max_tag_entries: 200
paperless:
  enabled: true
  inbox_path: /consume
  never_send:
    - resume
    - cv
""".strip()
    )
    return Classifier(str(config))


def test_unrelated_folder_and_tag_context_is_not_sent_to_ollama(tmp_path):
    classifier = make_classifier(tmp_path)
    classifier.global_instructions = """For Docker Compose files, identify the primary application represented by
    the compose stack.

Set suggested_folder to:
docker-compose/<primary-application>

Set suggested_filename to:
docker-compose.yaml
"""
    compose = """services:
  immich-server:
    image: ghcr.io/immich-app/immich-server:release
  redis:
    image: redis:7
  database:
    image: postgres:16
"""
    captured = {}

    def fake(prompt, organization_rules=""):
        captured["prompt"] = prompt
        return json.dumps({
            "suggested_filename": "immich-docker-compose.yml",
            "suggested_folder": "/Documents/Unsorted",
            "tags": ["immich"],
            "category": "other",
            "paperless_candidate": False,
            "confidence": 0.8,
            "reason": "compose file",
        })

    classifier._query_ollama = fake
    result = classifier.classify(
        "docker-compose (4).yml",
        "/AI Inbox/docker-compose (4).yml",
        compose,
        existing_folders=[
            "/Documents/Revature/Hexaware",
            "/Documents/Divorce",
            "/docker-compose/immich",
        ],
        existing_tags=["divorce", "Revature", "immich"],
    )

    assert "Revature" not in captured["prompt"]
    assert "Hexaware" not in captured["prompt"]
    assert "divorce" not in captured["prompt"].casefold()
    # Existing destination metadata must not appear in the identity-stage prompt.
    assert "/docker-compose/immich" not in captured["prompt"]
    assert result["category"] == "docker_compose"
    assert result["suggested_folder"] == "/docker-compose/immich"
    assert result["suggested_filename"] == "docker-compose.yaml"


def test_resume_identity_is_consistent_and_never_send_wins(tmp_path):
    classifier = make_classifier(tmp_path)
    resume_text = """Kenneth Stratton
Professional Summary
Automation Engineer
Professional Experience
Revature
Hexaware
Education
Technical Skills
Certifications
"""

    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "Kenneth_Stratton_Tax_Returns_2009-2024_CV.odt",
        "suggested_folder": "/Documents/Revature/Hexaware",
        "tags": ["professional"],
        "category": "employment_document",
        "paperless_candidate": True,
        "confidence": 0.94,
        "reason": "professional document",
    })

    original = "Kenneth Stratton Resume June 2025 skills formated.odt"
    result = classifier.classify(
        original,
        f"/AI Inbox/{original}",
        resume_text,
        existing_folders=["/Documents/Resumes", "/Documents/Revature/Hexaware"],
        existing_tags=["resume", "Revature", "Hexaware"],
    )

    assert result["category"] == "resume"
    assert result["paperless_candidate"] is False
    assert result["suggested_folder"] == "/Documents/Resumes"
    assert result["suggested_filename"] == original


def test_public_key_with_null_content_never_calls_ollama(tmp_path):
    classifier = make_classifier(tmp_path)

    def should_not_run(*args, **kwargs):
        raise AssertionError("Ollama must not be called when extracted content is null")

    classifier._query_ollama = should_not_run
    result = classifier.classify(
        "darth_public_key_rsa.txt",
        "/AI Inbox/darth_public_key_rsa.txt",
        "null",
        existing_folders=["/Documents/Divorce", "/Security/Keys"],
        existing_tags=["divorce"],
    )

    assert result["suggested_filename"] == "darth_public_key_rsa.txt"
    assert result["suggested_folder"] == "/Security/Keys"
    assert result["category"] == "public_key"
    assert result["paperless_candidate"] is False
    assert "divorce" not in result["tags"]


def test_resume_content_can_supply_hint_even_without_resume_filename(tmp_path):
    classifier = make_classifier(tmp_path)
    content = """Professional Summary
Work Experience
Education
Technical Skills
Certifications
"""
    assert classifier._deterministic_category_hint("Kenneth.odt", content) == "resume"


def test_category_vocabulary_does_not_include_destination_names(tmp_path):
    classifier = make_classifier(tmp_path)
    choices = classifier._category_choices()
    assert "resume" in choices
    assert "docker_compose" in choices
    assert "revature" not in choices
    assert "hexaware" not in choices
    assert "divorce" not in choices


def test_admin_template_can_create_missing_nested_folder(tmp_path):
    classifier = make_classifier(tmp_path)
    classifier.global_instructions = """For Docker Compose files, identify the primary application represented by
    the compose stack.

Set suggested_folder to:
docker-compose/<primary-application>

Set suggested_filename to:
docker-compose.yaml
"""
    compose = """services:
  immich-server:
    image: ghcr.io/immich-app/immich-server:release
  redis:
    image: redis:7
  database:
    image: postgres:16
"""
    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "immich-docker-compose.yml",
        "suggested_folder": "/Documents/Unsorted",
        "tags": ["immich"],
        "category": "other",
        "paperless_candidate": False,
        "confidence": 0.9,
        "reason": "compose file",
    })
    classifier._query_destination_ollama = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("Explicit admin folder template should not need destination Ollama")
    )
    result = classifier.classify(
        "docker-compose (4).yml",
        "/AI Inbox/docker-compose (4).yml",
        compose,
        existing_folders=["/Documents", "/docker-compose"],
        existing_tags=[],
    )
    assert result["category"] == "docker_compose"
    assert result["suggested_folder"] == "/docker-compose/immich"
    assert result["suggested_filename"] == "docker-compose.yaml"
    assert "intentional new folder" in result["reason"].casefold()


def test_destination_stage_can_choose_semantic_existing_folder(tmp_path):
    classifier = make_classifier(tmp_path)
    content = "Maryland Motor Vehicle Administration vehicle registration renewal."
    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "Vehicle_Registration.txt",
        "suggested_folder": "/Documents/Vehicle Registration",
        "tags": ["vehicle", "registration"],
        "category": "vehicle_registration",
        "paperless_candidate": False,
        "confidence": 0.91,
        "reason": "Vehicle registration record.",
    })
    seen = {}
    def destination(**kwargs):
        seen.update(kwargs)
        return {
            "destination_type": "existing",
            "suggested_folder": "/Documents/DMV",
            "confidence": 0.94,
            "reason": "DMV is the matching existing destination.",
        }
    classifier._query_destination_ollama = destination
    result = classifier.classify(
        "1234.txt", "/AI Inbox/1234.txt", content,
        existing_folders=["/Documents/DMV", "/Documents/Taxes"],
        existing_tags=[],
    )
    assert result["category"] == "vehicle_registration"
    assert result["suggested_folder"] == "/Documents/DMV"
    assert seen["category"] == "vehicle_registration"


def test_destination_stage_can_propose_new_folder_when_existing_do_not_match(tmp_path):
    classifier = make_classifier(tmp_path)
    content = "Maryland Motor Vehicle Administration vehicle registration renewal."
    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "Vehicle_Registration.txt",
        "suggested_folder": "/Documents/Vehicle Registration",
        "tags": ["vehicle", "registration"],
        "category": "vehicle_registration",
        "paperless_candidate": False,
        "confidence": 0.92,
        "reason": "Vehicle registration record.",
    })
    classifier._query_destination_ollama = lambda **kwargs: {
        "destination_type": "new",
        "suggested_folder": "/Documents/Vehicle Registration",
        "confidence": 0.93,
        "reason": "No existing folder is appropriate.",
    }
    result = classifier.classify(
        "1234.txt", "/AI Inbox/1234.txt", content,
        existing_folders=["/Documents/Taxes", "/Documents/Insurance"],
        existing_tags=[],
    )
    assert result["suggested_folder"] == "/Documents/Vehicle Registration"
    assert result["confidence"] > 0.69
    assert "intentional new folder" in result["reason"].casefold()


def test_no_existing_folders_uses_safe_new_path_without_second_ollama(tmp_path):
    classifier = make_classifier(tmp_path)
    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "Quarterly_Report.txt",
        "suggested_folder": "/Documents/Reports",
        "tags": ["report"],
        "category": "report",
        "paperless_candidate": False,
        "confidence": 0.9,
        "reason": "Quarterly report.",
    })
    classifier._query_destination_ollama = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("No existing folders means no destination comparison call is needed")
    )
    result = classifier.classify(
        "1234.txt", "/AI Inbox/1234.txt", "Quarterly operational report for the project.",
        existing_folders=[], existing_tags=[],
    )
    assert result["suggested_folder"] in {"/Documents/Reports", "/Documents/Report"}
    assert result["suggested_folder"] != "/Documents/Unsorted"


def test_strong_existing_folder_beats_new_generic_bill_path(tmp_path):
    classifier = make_classifier(tmp_path)
    classifier.paperless_prefer_send = {"invoice"}
    classifier._query_ollama = lambda prompt, organization_rules="": json.dumps({
        "suggested_filename": "Verizon_Phone_April.pdf",
        "suggested_folder": "/Documents/Bills/Verizon",
        "tags": ["verizon", "phone"],
        "category": "invoice",
        "paperless_candidate": True,
        "confidence": 0.96,
        "reason": "Verizon phone invoice.",
    })
    classifier._query_destination_ollama = lambda **kwargs: (_ for _ in ()).throw(
        AssertionError("Strong existing folder should be selected before destination Ollama")
    )

    result = classifier.classify(
        "verizon phone april.pdf",
        "/AI Inbox/verizon phone april.pdf",
        "Verizon Wireless monthly phone bill for April. Amount due $125.00.",
        existing_folders=[
            "/verizon bills/phone",
            "/Documents/Bills",
            "/Documents/Taxes",
        ],
        existing_tags=["verizon", "phone"],
    )

    assert result["suggested_folder"] == "/verizon bills/phone"
    assert result["paperless_candidate"] is True
