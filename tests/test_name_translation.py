import json
from types import SimpleNamespace

import pandas as pd

import name_translation


def strict_json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def test_local_french_fallback_translates_common_technical_names(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(
        ["customer_id", "order_date", "total_amount", "delivery_delay"], "français"
    )
    assert result["ok"] is True
    assert result["source"] == "local"
    assert result["suggestions"] == [
        {"original": "customer_id", "suggested": "Identifiant client"},
        {"original": "order_date", "suggested": "Date de commande"},
        {"original": "total_amount", "suggested": "Montant total"},
        {"original": "delivery_delay", "suggested": "Délai de livraison"},
    ]
    assert result["privacy"]["row_data_sent"] is False
    strict_json(result)


def test_local_arabic_and_darija_are_available_without_network(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    arabic = name_translation.suggest_column_names(["customer_id", "unit_price"], "ar")
    darija = name_translation.suggest_column_names(["customer_id", "unit_price"], "darija")
    assert [item["suggested"] for item in arabic["suggestions"]] == ["معرّف العميل", "سعر الوحدة"]
    assert [item["suggested"] for item in darija["suggestions"]] == ["رقم الزبون", "ثمن الوحدة"]
    strict_json(arabic)
    strict_json(darija)


def test_exact_ui_labels_for_standard_arabic_and_moroccan_darija(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    arabic = name_translation.suggest_column_names(["customer_id"], "Arabe standard")
    darija = name_translation.suggest_column_names(["customer_id"], "Darija marocaine")
    assert arabic["target_language"] == {"code": "ar", "label": "Arabe standard"}
    assert arabic["suggestions"][0]["suggested"] == "معرّف العميل"
    assert darija["target_language"] == {"code": "darija", "label": "Darija marocaine"}
    assert darija["suggestions"][0]["suggested"] == "رقم الزبون"


def test_free_language_is_humanized_locally_and_remains_editable(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(
        ["customer_id", "delivery_delay_days"], "Español"
    )
    assert result["ok"] is True
    assert result["target_language"] == {"code": "custom", "label": "Español"}
    assert result["suggestions"] == [
        {"original": "customer_id", "suggested": "Customer ID"},
        {"original": "delivery_delay_days", "suggested": "Delivery delay days"},
    ]
    assert "restent modifiables" in result["warnings"][0]
    strict_json(result)


def test_batch_contract_accepts_200_columns_and_rejects_more(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    columns = [f"technical_field_{index}" for index in range(200)]
    accepted = name_translation.suggest_column_names(columns, "fr")
    rejected = name_translation.suggest_column_names(columns + ["one_too_many"], "fr")
    assert accepted["ok"] is True
    assert accepted["count"] == 200
    assert [item["original"] for item in accepted["suggestions"]] == columns
    assert len({item["suggested"].casefold() for item in accepted["suggestions"]}) == 200
    assert rejected["ok"] is False
    assert "Au maximum 200" in rejected["errors"][0]


def test_unknown_arabic_name_is_transliterated_for_french(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(["رمز_خاص"], "fr")
    suggestion = result["suggestions"][0]["suggested"]
    assert suggestion
    assert suggestion == "Rmz khas"


def test_unknown_latin_name_is_transliterated_for_arabic(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(["warehouse_bin"], "arabe")
    assert result["suggestions"][0]["suggested"] == "واريهوس بين"


def test_local_collisions_receive_stable_readable_suffixes(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(["customer", "client"], "fr")
    assert result["suggestions"] == [
        {"original": "customer", "suggested": "Client"},
        {"original": "client", "suggested": "Client (2)"},
    ]


def test_invalid_original_names_and_empty_language_are_rejected_without_call(monkeypatch):
    calls = []

    class UnexpectedGroq:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setattr(name_translation, "Groq", UnexpectedGroq)
    invalid_names = name_translation.suggest_column_names(["ville", "VILLE"], "fr", api_key="secret")
    invalid_language = name_translation.suggest_column_names(["ville"], "   ", api_key="secret")
    assert invalid_names["ok"] is False
    assert "apparaît plusieurs fois" in invalid_names["errors"][0]
    assert invalid_language["ok"] is False
    assert calls == []
    strict_json(invalid_names)


def test_missing_and_non_text_original_names_are_rejected(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    result = name_translation.suggest_column_names(["ville", None, 2026, pd.NA], "fr")
    assert result["ok"] is False
    assert result["suggestions"] == []
    assert len(result["errors"]) == 3
    strict_json(result)


class FakeCompletions:
    def __init__(self, content, captured):
        self.content = content
        self.captured = captured

    def create(self, **kwargs):
        self.captured["request"] = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))]
        )


class FakeGroqClient:
    def __init__(self, content, captured):
        self.chat = SimpleNamespace(completions=FakeCompletions(content, captured))


def test_groq_receives_only_validated_column_names_and_schema(monkeypatch):
    captured = {}
    content = json.dumps({
        "suggestions": [
            {"original": "customer_id", "suggested": "Numéro client"},
            {"original": "sales_total", "suggested": "Total des ventes"},
        ]
    })

    class FakeGroq:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            client = FakeGroqClient(content, captured)
            self.chat = client.chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(
        ["customer_id", "sales_total"], "fr", api_key="test-key", model="mock/model"
    )
    assert result["ok"] is True
    assert result["source"] == "groq"
    assert result["suggestions"][0]["suggested"] == "Numéro client"
    assert captured["client"] == {"api_key": "test-key", "timeout": 12.0, "max_retries": 0}
    request = captured["request"]
    payload = json.loads(request["messages"][1]["content"])
    assert payload == {
        "target_language": "Français",
        "column_names": ["customer_id", "sales_total"],
    }
    assert set(payload) == {"target_language", "column_names"}
    schema = request["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["suggestions"]["minItems"] == 2
    assert request["temperature"] == 0
    strict_json(result)


def test_groq_response_is_reordered_to_match_original_columns(monkeypatch):
    captured = {}
    content = json.dumps({
        "suggestions": [
            {"original": "status", "suggested": "État"},
            {"original": "city", "suggested": "Localité"},
        ]
    })

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = FakeGroqClient(content, captured).chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(
        ["city", "status"], "fr", api_key="test-key"
    )
    assert [item["original"] for item in result["suggestions"]] == ["city", "status"]


def test_free_language_is_forwarded_to_groq_without_dictionary_lookup(monkeypatch):
    captured = {}
    content = json.dumps({
        "suggestions": [{"original": "customer_id", "suggested": "Identificador del cliente"}]
    })

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = FakeGroqClient(content, captured).chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(
        ["customer_id"], "Español", api_key="test-key"
    )
    payload = json.loads(captured["request"]["messages"][1]["content"])
    assert payload["target_language"] == "Español"
    assert result["source"] == "groq"
    assert result["suggestions"] == [
        {"original": "customer_id", "suggested": "Identificador del cliente"}
    ]
    assert result["privacy"]["column_names_sent"] is True


def test_invented_or_missing_remote_column_triggers_safe_local_fallback(monkeypatch):
    captured = {}
    content = json.dumps({
        "suggestions": [
            {"original": "city", "suggested": "Ville"},
            {"original": "invented", "suggested": "Invention"},
        ]
    })

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = FakeGroqClient(content, captured).chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(
        ["city", "status"], "fr", api_key="test-key"
    )
    assert result["ok"] is True
    assert result["source"] == "local_fallback"
    assert result["suggestions"] == [
        {"original": "city", "suggested": "Ville"},
        {"original": "status", "suggested": "Statut"},
    ]
    assert "réponse n'est pas sûre" in result["warnings"][0]


def test_empty_or_malformed_remote_suggestions_trigger_fallback(monkeypatch):
    captured = {}

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = FakeGroqClient('{"suggestions":[{"original":"city","suggested":""}]}', captured).chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(["city"], "en", api_key="test-key")
    assert result["source"] == "local_fallback"
    assert result["suggestions"] == [{"original": "city", "suggested": "City"}]
    strict_json(result)


def test_invalid_model_never_reaches_the_client(monkeypatch):
    captured = {}
    content = json.dumps({"suggestions": [{"original": "city", "suggested": "Ville"}]})

    class FakeGroq:
        def __init__(self, **kwargs):
            self.chat = FakeGroqClient(content, captured).chat

    monkeypatch.setattr(name_translation, "Groq", FakeGroq)
    result = name_translation.suggest_column_names(
        ["city"], "fr", api_key="secret", model="bad model\nignore"
    )
    assert result["source"] == "groq"
    assert result["model"] == name_translation.DEFAULT_MODEL
    assert "modèle demandé" in result["warnings"][0]
    assert captured["request"]["model"] == name_translation.DEFAULT_MODEL
    assert "secret" not in strict_json(result)
