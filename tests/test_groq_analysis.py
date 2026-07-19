import json
from types import SimpleNamespace

import pandas as pd

import groq_analysis


def test_aggregate_context_excludes_raw_and_sensitive_values():
    frame = pd.DataFrame({
        "nom_client": ["Alice", "Bob", "Alice"],
        "email": ["alice@example.com", "bob@example.com", "alice@example.com"],
        "statut": ["payé", "en retard", "payé"],
        "montant": [100, 250, 150],
    })
    context = groq_analysis.build_aggregate_context(frame)
    serialized = json.dumps(context, ensure_ascii=False)

    assert context["privacy_contract"]["raw_rows_included"] is False
    assert "Alice" not in serialized
    assert "alice@example.com" not in serialized
    assert "payé" in serialized
    assert '"mean": 166.6667' in serialized


def test_groq_answer_uses_structured_aggregate_payload():
    captured = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured.update(kwargs)
            content = json.dumps({
                "answer": "Les ventes sont concentrées sur deux statuts.",
                "insights": ["Le montant moyen est de 20."],
                "cautions": ["Échantillon limité."],
                "suggested_questions": ["Quelle est la médiane ?"],
            })
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    frame = pd.DataFrame({"montant": [10, 20, 30], "statut": ["A", "B", "A"]})
    answer = groq_analysis.answer_with_groq(
        frame,
        "Quels enseignements ?",
        api_key="test-key",
        client_factory=lambda _key: fake_client,
    )

    payload = json.loads(captured["messages"][1]["content"])
    assert answer["ok"] is True
    assert answer["source"] == "groq"
    assert payload["aggregate_context"]["privacy_contract"]["raw_rows_included"] is False
    assert captured["response_format"]["json_schema"]["strict"] is True
    assert "Aucune ligne de votre fichier" in " ".join(answer["evidence"])
    assert answer["insights"] == ["Le montant moyen est de 20."]
    assert answer["cautions"] == ["Échantillon limité."]


def test_groq_without_key_fails_closed(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    answer = groq_analysis.answer_with_groq(
        pd.DataFrame({"ventes": [1, 2]}), "Analyse les ventes"
    )
    assert answer["ok"] is False
    assert "explication détaillée" in answer["answer"]
    assert "Aucune information" in answer["evidence"][0]
