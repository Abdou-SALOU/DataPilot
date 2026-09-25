import io
import re
from pathlib import Path

import app as datapilot_app
import i18n


ROOT = Path(__file__).resolve().parents[1]


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(datapilot_app, "STORAGE_ROOT", tmp_path / "storage")
    datapilot_app.app.config.update(TESTING=True, SECRET_KEY="test-secret")
    return datapilot_app.app.test_client()


def test_every_template_string_has_an_english_translation():
    missing = set()
    for template in (ROOT / "templates").glob("*.html"):
        for text in re.findall(r"_\('((?:[^'\\]|\\.)*)'\)", template.read_text(encoding="utf-8")):
            if i18n.gettext(text, "en") == text and text not in {"Pipeline", "SQL", "Bronze", "Silver", "Gold", "Sources", "Documents", "Date", "Impact", "Table", "Assistant", "Unique", "tables", "Photo · PDF · Excel · CSV", "Excellent"}:
                missing.add(text)
    assert not missing, sorted(missing)


def test_section_labels_and_generated_messages_are_translated():
    for section in datapilot_app.PROJECT_SECTIONS.values():
        for key in ("label", "description", "title", "guidance"):
            assert i18n.gettext(section[key], "en") != section[key] or section[key] in {"Pipeline", "SQL"}
    assert i18n.gettext("Convertir prix en nombre", "en") == "Convert prix to number"
    assert i18n.gettext("3 doublon(s) exact(s)", "en") == "3 exact duplicate(s)"
    assert i18n.gettext("Répartition · ville", "en") == "Breakdown · ville"
    assert i18n.gettext("Texte inconnu", "en") == "Texte inconnu"


def test_english_questions_reach_the_local_assistant():
    assert "combien de lignes" in i18n.translate_question_to_fr("How many rows are in this file?")
    assert "doublon" in i18n.translate_question_to_fr("Any duplicates?")


def test_language_switch_sets_cookie_and_renders_english(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    french = test_client.get("/").get_data(as_text=True)
    assert '<html lang="fr">' in french and "Essayer la démo" in french
    response = test_client.get("/lang/en?next=/")
    assert response.status_code == 302 and response.headers["Location"].endswith("/")
    assert "dp_lang=en" in response.headers["Set-Cookie"]
    english = test_client.get("/").get_data(as_text=True)
    assert '<html lang="en">' in english
    assert "Try the live demo" in english
    assert "Essayer la démo" not in english


def test_language_switch_refuses_external_redirects(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    response = test_client.get("/lang/en?next=//evil.example")
    assert response.headers["Location"].endswith("/")
    assert "evil" not in response.headers["Location"]
    assert test_client.get("/lang/de").status_code == 404


def test_project_pages_render_in_english(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    test_client.get("/")
    with test_client.session_transaction() as session:
        token = session["_csrf"]
    upload = test_client.post(
        "/upload",
        data={"_csrf": token, "dataset": (io.BytesIO(b"ville,montant\n Casa ,10\nCasa,10\nRabat,\n"), "ventes.csv")},
        content_type="multipart/form-data",
    )
    project_url = upload.headers["Location"]
    test_client.set_cookie("dp_lang", "en")
    overview = test_client.get(project_url).get_data(as_text=True)
    assert "Quality score" in overview and "Automatic dashboard" in overview
    cleaning = test_client.get(project_url + "/cleaning").get_data(as_text=True)
    assert "Suggested fixes" in cleaning
    assert "Trim extra spaces" in cleaning
    pipeline_page = test_client.get(project_url + "/pipeline").get_data(as_text=True)
    assert "Run history" in pipeline_page
    assert "window.DP_I18N" in pipeline_page and "Analyzing" in pipeline_page
