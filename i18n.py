# -*- coding: utf-8 -*-
"""Interface bilingue français / anglais de DataPilot.

Le français reste la langue source du code et des tests. L'anglais est servi à
l'affichage, à partir de deux mécanismes :

* un catalogue exact ``CATALOG_EN`` (texte français → texte anglais) ;
* des motifs ``PATTERNS_EN`` pour les phrases générées avec des valeurs
  (« Convertir prix en nombre », « 3 doublon(s) exact(s) »…).

Les calculs ne dépendent jamais de la langue : seule la présentation change.
"""

from __future__ import annotations

import re
from typing import Any, Callable

SUPPORTED_LANGUAGES = ("fr", "en")
DEFAULT_LANGUAGE = "fr"
COOKIE_NAME = "dp_lang"


def current_language() -> str:
    """Langue de la requête en cours (cookie), français par défaut."""
    try:
        from flask import g, has_request_context, request
    except ImportError:  # pragma: no cover - Flask est une dépendance du projet
        return DEFAULT_LANGUAGE
    if not has_request_context():
        return DEFAULT_LANGUAGE
    forced = getattr(g, "dp_lang", None)
    if forced in SUPPORTED_LANGUAGES:
        return forced
    value = request.args.get("lang") or request.cookies.get(COOKIE_NAME, "")
    return value if value in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


def gettext(text: object, lang: str | None = None) -> str:
    """Traduit un texte d'interface. Sans équivalent connu, le texte est conservé."""
    source = "" if text is None else str(text)
    language = lang or current_language()
    if language == "fr" or not source:
        return source
    exact = CATALOG_EN.get(source)
    if exact is not None:
        return exact
    return _translate_patterns(source)


def translate(value: Any, lang: str | None = None) -> Any:
    """Traduit récursivement une structure (dict, list, str) destinée à l'affichage."""
    language = lang or current_language()
    if language == "fr":
        return value
    if isinstance(value, str):
        return gettext(value, language)
    if isinstance(value, list):
        return [translate(item, language) for item in value]
    if isinstance(value, dict):
        return {key: translate(item, language) if key in TRANSLATABLE_KEYS else item for key, item in value.items()}
    return value


# Clés de dictionnaires dont la valeur est un texte à afficher (jamais une donnée).
TRANSLATABLE_KEYS = {
    "label", "description", "impact", "title", "subtitle", "dataset_label", "caution",
    "points", "detail", "reason", "total_label", "findings", "actions", "limitations",
    "answer", "insights", "cautions", "evidence", "suggested_questions", "chart_message",
    "message", "status_label", "favorable_direction_label", "aggregation_label",
    "guidance", "next_label", "text",
}


def _translate_patterns(text: str) -> str:
    for pattern, replacement in _COMPILED_PATTERNS:
        match = pattern.fullmatch(text)
        if match:
            if callable(replacement):
                return replacement(match)
            return match.expand(replacement)
    # Phrases composées : on traduit chaque proposition séparée par « . » ou « ; ».
    if ". " in text or "; " in text:
        parts = re.split(r"(?<=[.;])\s+", text)
        if len(parts) > 1:
            translated = [gettext(part, "en") for part in parts]
            if translated != parts:
                return " ".join(translated)
    return text


def translate_question_to_fr(question: str) -> str:
    """Rend une question anglaise compréhensible par l'assistant local (règles françaises)."""
    text = f" {question.strip().lower()} "
    for source, target in QUESTION_EN_TO_FR:
        text = re.sub(source, target, text)
    return text.strip()


# ---------------------------------------------------------------------------
# Catalogue exact
# ---------------------------------------------------------------------------

CATALOG_EN: dict[str, str] = {
    # Navigation et gabarit
    "Aller au contenu": "Skip to content",
    "Accueil DataPilot": "DataPilot home",
    "Vos données, en clair.": "Your data, made clear.",
    "Fonctionnement": "How it works",
    "Commencer": "Get started",
    "Mes analyses": "My analyses",
    "Nouvelle analyse": "New analysis",
    "Langue": "Language",
    "Des fichiers bruts aux décisions fiables.": "From raw files to trusted decisions.",
    "Traitement local · Décisions sous contrôle humain": "Local processing · Human-in-the-loop decisions",
    "Projet open source d’Abdou SALOU": "Open-source project by Abdou SALOU",
    # Accueil
    "Plateforme de qualité des données": "Data quality platform",
    "Des fichiers désordonnés": "Messy files in.",
    "aux données fiables.": "Trusted data out.",
    "DataPilot ingère vos tableurs, PDF et photos de carnets, contrôle chaque colonne avec un contrat de données, propose les corrections et publie des tables prêtes pour la BI, le SQL et l’API.":
        "DataPilot ingests spreadsheets, PDFs and photos of handwritten ledgers, validates every column against a data contract, proposes fixes and publishes tables ready for BI, SQL and APIs.",
    "Essayer la démo": "Try the live demo",
    "Importer un fichier": "Import a file",
    "900 ventes réelles simulées · 39 contrôles": "900 simulated sales · 39 checks",
    "Sources": "Sources",
    "Bronze": "Bronze",
    "Silver": "Silver",
    "Gold": "Gold",
    "Contrat": "Contract",
    "Sorties": "Outputs",
    "Données brutes": "Raw data",
    "Données typées et corrigées": "Typed & cleaned",
    "Tables métier": "Business tables",
    "Contrôles qualité": "Quality checks",
    "Tableaux de bord · SQL · API · Excel": "Dashboards · SQL · API · Excel",
    "Photo · PDF · Excel · CSV": "Photo · PDF · Excel · CSV",
    "contrôles réussis": "checks passed",
    "Qualité avant → après": "Quality before → after",
    "Choisissez votre point de départ": "Choose your starting point",
    "Trois entrées, un même pipeline contrôlé.": "Three entry points, one governed pipeline.",
    "Tableur": "Spreadsheet",
    "CSV, Excel ou JSON jusqu’à 20 Mo et 100 000 lignes.": "CSV, Excel or JSON up to 20 MB and 100,000 rows.",
    "Déposez votre fichier ici": "Drop your file here",
    "ou cliquez pour le sélectionner": "or click to browse",
    "Aucun fichier sélectionné": "No file selected",
    "Analyser le fichier": "Analyze file",
    "Analyse en cours…": "Analyzing…",
    "Photos et carnets manuscrits": "Photos & handwritten ledgers",
    "Jusqu’à 8 documents : photo, PDF scanné, TXT, Excel. L’OCR local propose des lignes que vous validez.":
        "Up to 8 documents: photo, scanned PDF, TXT, Excel. Local OCR proposes rows that you validate.",
    "Ventes": "Sales",
    "Dépenses": "Expenses",
    "Monnaie": "Currency",
    "Autre ou inconnue": "Other or unknown",
    "Documents": "Documents",
    "Prendre une photo": "Take a photo",
    "Lire les documents": "Read documents",
    "Lecture des documents…": "Reading documents…",
    "Factures fournisseurs": "Supplier invoices",
    "Jusqu’à 10 factures PDF ou photos. Fournisseur, date, HT, TVA et TTC sont extraits puis vérifiés par vous.":
        "Up to 10 PDF or photo invoices. Supplier, date, net, VAT and total are extracted, then verified by you.",
    "Extraire les factures": "Extract invoices",
    "Lecture des factures…": "Reading invoices…",
    "Préparation de la démo…": "Preparing demo…",
    "Aucun envoi en ligne : les fichiers restent sur cet ordinateur.": "Nothing is uploaded online: files stay on this computer.",
    "Ce que fait DataPilot": "What DataPilot does",
    "L’ingénierie d’un data engineer, l’interface d’un produit grand public.": "Data engineering rigor with a consumer-grade interface.",
    "Contrat de données": "Data contract",
    "Types, champs obligatoires, unicité, plages et libellés cohérents sont déduits puis contrôlés sur chaque exécution.":
        "Types, required fields, uniqueness, ranges and consistent labels are inferred, then enforced on every run.",
    "Architecture médaillon": "Medallion architecture",
    "Bronze, Silver et Gold en Parquet : les données brutes ne sont jamais écrasées.":
        "Bronze, Silver and Gold in Parquet: raw data is never overwritten.",
    "Lineage et journal": "Lineage & run log",
    "Chaque exécution est tracée : empreinte SHA-256 du fichier, durée par étape, lignes entrées et sorties.":
        "Every run is traced: file SHA-256 fingerprint, duration per stage, rows in and out.",
    "Console SQL": "SQL console",
    "Interrogez Bronze, Silver et Gold avec DuckDB, en lecture seule et sans accès disque.":
        "Query Bronze, Silver and Gold with DuckDB, read-only and sandboxed from the file system.",
    "OCR avec validation humaine": "Human-in-the-loop OCR",
    "Photos de carnets et PDF scannés lus localement, chaque ligne confirmée avant tout calcul.":
        "Ledger photos and scanned PDFs read locally, every row confirmed before any calculation.",
    "API et exports": "API & exports",
    "API JSON, Excel sécurisé contre l’injection de formules, CSV et JSON.":
        "JSON API, Excel export hardened against formula injection, CSV and JSON.",
    "Analyses récentes": "Recent analyses",
    "Retrouvez les fichiers déjà analysés sur cet ordinateur.": "Pick up files already analyzed on this computer.",
    "Qualité": "Quality",
    "corrections à valider": "fixes to review",
    "lignes": "rows",
    "Date inconnue": "Unknown date",
    # Projet : en-tête et navigation
    "Analyse prête": "Analysis ready",
    "Exporter Excel": "Export Excel",
    "Autres formats": "Other formats",
    "Fichier CSV": "CSV file",
    "Fichier JSON": "JSON file",
    "Préparation du fichier Excel…": "Preparing Excel file…",
    "Préparation du téléchargement…": "Preparing download…",
    "colonnes": "columns",
    "version nettoyée": "cleaned version",
    "fichier d’origine": "original file",
    "Dernière exécution": "Last run",
    "Pages de l’analyse": "Analysis pages",
    "Vue d’ensemble": "Overview",
    "Qualité & corrections": "Quality & fixes",
    "Pipeline": "Pipeline",
    "SQL": "SQL",
    "Graphiques": "Charts",
    "Libellés": "Labels",
    "Indicateurs et synthèse": "KPIs and summary",
    "Contrat et nettoyage": "Contract and cleaning",
    "Couches et lineage": "Layers and lineage",
    "Interroger les tables": "Query the tables",
    "Créer des visuels": "Build visuals",
    "Renommer pour le métier": "Business-friendly names",
    "Assistant": "Assistant",
    "Posez une question": "Ask a question",
    "Étape": "Step",
    "sur": "of",
    "Continuer": "Continue",
    "Suivant": "Next",
    # Vue d'ensemble
    "Score qualité": "Quality score",
    "Lignes": "Rows",
    "Colonnes": "Columns",
    "Cellules vides": "Empty cells",
    "Doublons": "Duplicates",
    "colonnes détectées": "columns detected",
    "cellules à contrôler": "cells to review",
    "lignes identiques": "identical rows",
    "Contrôles réussis": "Checks passed",
    "sur Silver": "on Silver",
    "Aide à la décision": "Decision support",
    "Que disent vraiment vos chiffres ?": "What do your numbers really say?",
    "Calculs locaux, méthode et limites visibles.": "Local calculations, with method and limits shown.",
    "Écouter": "Listen",
    "lignes retenues": "rows used",
    "Méthode": "Method",
    "Prochaine vérification utile": "Next useful check",
    "Limites de cette analyse": "Limits of this analysis",
    "Tableau de bord automatique": "Automatic dashboard",
    "Vos données en un coup d’œil": "Your data at a glance",
    "Les vues sont choisies selon le type de chaque colonne.": "Views are chosen from each column’s data type.",
    "Vue automatique": "Auto view",
    "Comprendre le point le plus élevé": "Understand the highest point",
    "Voir les valeurs": "Show values",
    "Élément": "Item",
    "Valeur": "Value",
    "Relations entre vos chiffres": "Relationships between your figures",
    "Les données ne contiennent pas encore assez de valeurs pour générer un graphique fiable.":
        "The data doesn’t contain enough values yet to build a reliable chart.",
    "Le graphique ne peut pas être affiché pour le moment.": "The chart can’t be displayed right now.",
    "Visualisation générée à partir des valeurs disponibles dans le fichier.": "Visualization generated from the values available in the file.",
    "Mes chiffres à suivre": "Tracked metrics",
    "Choisissez un chiffre important pour votre activité. DataPilot le recalcule lorsque le fichier change.":
        "Pick a metric that matters to your business. DataPilot recalculates it whenever the file changes.",
    "Aucun chiffre personnel pour le moment. Ajoutez par exemple votre total de ventes ou votre panier moyen.":
        "No tracked metric yet. Add, for example, your total sales or average basket.",
    "Ajouter un chiffre à suivre": "Add a tracked metric",
    "Comment voulez-vous l’appeler ?": "What do you want to call it?",
    "Ex. Objectif de ventes mensuel": "e.g. Monthly sales target",
    "Quel chiffre utiliser ?": "Which figure?",
    "Nombre de lignes": "Number of rows",
    "Comment le calculer ?": "How to calculate it?",
    "Compter": "Count",
    "Faire le total": "Sum",
    "Calculer la moyenne": "Average",
    "Trouver la valeur centrale": "Median",
    "Quel est votre objectif ?": "What is your target?",
    "Ex. 10000": "e.g. 10000",
    "Quel sens est favorable ?": "Which direction is good?",
    "Plus le chiffre est élevé, mieux c’est": "Higher is better",
    "Plus le chiffre est bas, mieux c’est": "Lower is better",
    "Ajouter à mon résumé": "Add to summary",
    "Ajout…": "Adding…",
    "Retrait…": "Removing…",
    "Retirer": "Remove",
    "Retirer ce chiffre du résumé ?": "Remove this metric from the summary?",
    "Objectif": "Target",
    "Aucun objectif défini.": "No target set.",
    # Qualité
    "Contrat de données : avant et après nettoyage": "Data contract: before and after cleaning",
    "Chaque règle est évaluée sur la couche Bronze (brute) puis Silver (nettoyée).":
        "Each rule is evaluated on the Bronze (raw) layer, then on Silver (cleaned).",
    "Règle": "Rule",
    "Colonne": "Column",
    "Détail": "Detail",
    "Réussi": "Passed",
    "Alerte": "Warning",
    "Échec": "Failed",
    "Table": "Table",
    "réussis": "passed",
    "alertes": "warnings",
    "échecs": "failures",
    "Afficher uniquement les écarts": "Show issues only",
    "Afficher toutes les règles": "Show all rules",
    "À vérifier": "To review",
    "Tout est en ordre": "All clear",
    "Corrections proposées": "Suggested fixes",
    "Aucune correction nécessaire": "No fix needed",
    "Cochez seulement les améliorations que vous souhaitez appliquer.": "Tick only the improvements you want to apply.",
    "Votre fichier ne contient plus d’erreur simple à corriger automatiquement.": "Your file no longer contains simple errors that can be fixed automatically.",
    "Appliquer les corrections sélectionnées": "Apply selected fixes",
    "Corrections en cours…": "Applying fixes…",
    "✓ Aucune correction sûre supplémentaire n’est proposée.": "✓ No further safe fix is proposed.",
    "Plus d’options": "More options",
    "Revenir au fichier d’origine": "Revert to original file",
    "Restauration…": "Restoring…",
    "Historique réversible": "Reversible history",
    "Vos étapes restent modifiables": "Your steps remain editable",
    "Vous pouvez annuler la plus récente ou la transformer en modèle réutilisable.": "Undo the latest one or save it as a reusable recipe.",
    "Annuler la dernière étape": "Undo last step",
    "Annulation…": "Undoing…",
    "Nom du modèle": "Recipe name",
    "Ex. Nettoyage mensuel": "e.g. Monthly cleanup",
    "Enregistrer": "Save",
    "Enregistrement…": "Saving…",
    "Réutiliser un modèle de correction": "Reuse a cleaning recipe",
    "Utiliser": "Apply",
    "Application…": "Applying…",
    "fort": "high",
    "conseille": "suggested",
    "Impact": "Impact",
    # Pipeline
    "Pipeline de données": "Data pipeline",
    "Ingestion, validation, transformation et publication, rejouables et traçables.":
        "Ingestion, validation, transformation and publishing: replayable and traceable.",
    "Relancer le pipeline": "Re-run pipeline",
    "Exécution…": "Running…",
    "Ingestion → Bronze": "Ingest → Bronze",
    "Validation du contrat": "Validate contract",
    "Transformation → Silver": "Transform → Silver",
    "Publication → Gold": "Publish → Gold",
    "lignes entrées": "rows in",
    "lignes sorties": "rows out",
    "Couches du lakehouse": "Lakehouse layers",
    "Fichiers Parquet": "Parquet files",
    "Brut, tel qu’ingéré, avec colonnes d’audit.": "Raw as ingested, with audit columns.",
    "Typé, corrigé, conforme au contrat.": "Typed, cleaned, contract-compliant.",
    "Agrégats métier prêts pour la BI.": "Business aggregates ready for BI.",
    "tables": "tables",
    "tables Parquet": "Parquet tables",
    "Historique des exécutions": "Run history",
    "Exécution": "Run",
    "Déclencheur": "Trigger",
    "Début": "Started",
    "Durée": "Duration",
    "Empreinte SHA-256": "SHA-256 fingerprint",
    "Qualité Bronze → Silver": "Quality Bronze → Silver",
    "import": "import",
    "demo": "demo",
    "cleaning": "cleaning",
    "undo": "undo",
    "reset": "reset",
    "recipe": "recipe",
    "manual": "manual",
    "backfill": "backfill",
    "documents": "documents",
    "invoices": "invoices",
    "Contrat inféré": "Inferred contract",
    "Type attendu": "Expected type",
    "Obligatoire": "Required",
    "Unique": "Unique",
    "Plage": "Range",
    "Catégoriel": "Categorical",
    "oui": "yes",
    "non": "no",
    "API de service": "Serving API",
    "Les couches sont exposées en JSON pour un outil BI, un notebook ou une autre application.":
        "Layers are exposed as JSON for a BI tool, a notebook or another application.",
    "Aucune exécution pour le moment.": "No run yet.",
    # SQL
    "Console SQL (DuckDB)": "SQL console (DuckDB)",
    "Requêtes SELECT en lecture seule sur Bronze, Silver et Gold. 200 lignes maximum affichées.":
        "Read-only SELECT queries on Bronze, Silver and Gold. Up to 200 rows displayed.",
    "Exemples": "Examples",
    "Exécuter": "Run query",
    "Exécution de la requête…": "Running query…",
    "Tables disponibles": "Available tables",
    "Résultat": "Result",
    "ligne(s)": "row(s)",
    "résultat tronqué": "truncated result",
    "Aucune ligne ne correspond à cette requête.": "No row matches this query.",
    "Top produits": "Top products",
    "Tendance mensuelle": "Monthly trend",
    "Profil des colonnes": "Column profile",
    "Aperçu Silver": "Silver preview",
    "Contrôle des doublons": "Duplicate check",
    # Graphiques
    "Vos données en images": "Your data as visuals",
    "Créez un graphique simplement": "Build a chart in seconds",
    "Choisissez un élément. DataPilot s’occupe du calcul et de la présentation.": "Pick a field. DataPilot handles the calculation and layout.",
    "Que voulez-vous voir ?": "What do you want to see?",
    "Choisissez une colonne. Le type de graphique le plus lisible sera sélectionné automatiquement.":
        "Pick a column. The most readable chart type is selected automatically.",
    "Comparer des éléments": "Compare items",
    "Suivre une évolution": "Track a trend",
    "Voir une répartition": "See a breakdown",
    "Quel élément voulez-vous regarder ?": "Which field do you want to look at?",
    "Choisir dans le fichier…": "Choose from the file…",
    "Options de calcul": "Calculation options",
    "Facultatif": "Optional",
    "Quel calcul ?": "Which calculation?",
    "Compter les lignes": "Count rows",
    "Calculer la somme": "Sum",
    "Quelle valeur ?": "Which value?",
    "Aucune pour ce calcul": "None for this calculation",
    "Quelle présentation ?": "Which chart type?",
    "Laisser DataPilot choisir": "Let DataPilot choose",
    "Barres": "Bars",
    "Courbe": "Line",
    "Anneau": "Doughnut",
    "Créer mon graphique": "Create chart",
    "Création du graphique…": "Creating chart…",
    "Votre sélection": "Your selection",
    "Graphiques enregistrés": "Saved charts",
    "Ils restent disponibles avec ce fichier.": "They stay available with this file.",
    "Créé par vous": "Created by you",
    "Télécharger PNG": "Download PNG",
    "Supprimer": "Delete",
    "Supprimer définitivement ce graphique ?": "Permanently delete this chart?",
    "Suppression…": "Deleting…",
    "Graphique calculé à partir de votre sélection.": "Chart calculated from your selection.",
    "Le graphique est prêt.": "The chart is ready.",
    # Assistant
    "Assistant DataPilot": "DataPilot assistant",
    "Ouvrir l’assistant DataPilot": "Open the DataPilot assistant",
    "DataPilot vous aide": "DataPilot can help",
    "Réponse immédiate": "Instant answer",
    "Fermer l’assistant": "Close assistant",
    "Fichier actif": "Active file",
    "Bonjour ! Posez-moi une question simple sur votre fichier.": "Hi! Ask me a simple question about your file.",
    "Votre explication approfondie": "Your in-depth explanation",
    "Votre réponse": "Your answer",
    "Réponse indisponible": "Answer unavailable",
    "À retenir": "Key takeaways",
    "Vous pouvez aussi demander :": "You can also ask:",
    "Questions suggérées": "Suggested questions",
    "Que faut-il retenir de ce fichier ?": "What are the key takeaways from this file?",
    "Que dois-je vérifier en priorité ?": "What should I check first?",
    "Propose un graphique utile": "Suggest a useful chart",
    "Un graphique": "A chart",
    "Votre question sur ce fichier": "Your question about this file",
    "Posez une question sur ce fichier…": "Ask a question about this file…",
    "Envoyer la question": "Send question",
    "Votre fichier reste sur cet ordinateur.": "Your file stays on this computer.",
    "Options de réponse": "Answer options",
    "Niveau": "Level",
    "Réponse rapide": "Quick answer",
    "Réponse approfondie": "In-depth answer",
    "Analyse de votre question…": "Analyzing your question…",
    "Besoin d’aide ?": "Need help?",
    "Demandez à DataPilot": "Ask DataPilot",
    # Libellés (dictionnaire)
    "Des mots simples": "Plain words",
    "Simplifiez un nom compliqué": "Simplify a complicated name",
    "Le fichier original reste intact. Vous vérifiez toujours le résultat avant de confirmer.":
        "The original file stays intact. You always review the result before confirming.",
    "Les trois étapes": "The three steps",
    "Choisissez": "Choose",
    "un nom du fichier": "a name from the file",
    "Vérifiez": "Review",
    "la proposition": "the suggestion",
    "Confirmez": "Confirm",
    "le changement": "the change",
    "Quel nom est difficile à comprendre ?": "Which name is hard to understand?",
    "Quel nom préférez-vous ?": "Which name do you prefer?",
    "Proposé automatiquement": "Suggested automatically",
    "Vous pouvez modifier la proposition.": "You can edit the suggestion.",
    "Renommer aussi les valeurs": "Also rename the values",
    "Facultatif · par exemple « cash » → « Espèces »": "Optional · for example “cash” → “Cash payment”",
    "Vérifier le résultat": "Review result",
    "Rien ne change avant votre confirmation.": "Nothing changes until you confirm.",
    "Préparation…": "Preparing…",
    "Dernière vérification": "Final check",
    "Est-ce bien le résultat souhaité ?": "Is this the result you want?",
    "Avant": "Before",
    "Après": "After",
    "Lignes ": "Rows ",
    "Si plusieurs anciens noms reçoivent le même nouveau nom, les regrouper dans les graphiques et les exports.":
        "If several old names get the same new name, group them in charts and exports.",
    "Confirmer le changement": "Confirm change",
    "Modifier": "Edit",
    "Noms déjà modifiés": "Names already changed",
    "Rétablir": "Restore",
    "Rétablissement…": "Restoring…",
    # Documents
    "Retour": "Back",
    "Étape 2 sur 3 · Vérifier": "Step 2 of 3 · Review",
    "Vérifiez les lignes lues avant de continuer": "Review the extracted rows before continuing",
    "Une photo peut être mal lue. Comparez les lignes ci-dessous avec vos documents. Vous pouvez tout corriger.":
        "A photo can be misread. Compare the rows below with your documents. You can fix everything.",
    "Ce lot contient plus de 100 lignes. Seules les 100 premières sont proposées : importez la suite dans un autre lot.":
        "This batch has more than 100 rows. Only the first 100 are proposed: import the rest in another batch.",
    "Écouter les consignes": "Listen to instructions",
    "Documents ajoutés": "Added documents",
    "ligne(s) proposées. À vérifier une par une.": "proposed row(s). Review them one by one.",
    "Ouvrir le document": "Open document",
    "Mes ventes": "My sales",
    "Mes dépenses": "My expenses",
    "Une ligne = une vente ou une dépense. Le montant est le total de la ligne.": "One row = one sale or expense. The amount is the row total.",
    "Ligne": "Row",
    "Date": "Date",
    "facultatif": "optional",
    "Article ou motif": "Item or reason",
    "Ex. Pain": "e.g. Bread",
    "Quantité": "Quantity",
    "Total payé": "Total paid",
    "Ex. 25,50": "e.g. 25.50",
    "Retirer cette ligne": "Remove this row",
    "+ Ajouter une ligne": "+ Add a row",
    "Les lignes sans article et sans montant seront ignorées. Aucun chiffre proposé par l’ordinateur n’est utilisé avant votre confirmation.":
        "Rows without item and amount are ignored. No computer-proposed figure is used before your confirmation.",
    "J’ai vérifié · Voir l’analyse": "I’ve checked · See analysis",
    "Préparation du résumé…": "Preparing summary…",
    "Confiance OCR": "OCR confidence",
    "Lu par OCR": "Read by OCR",
    "Comparez chaque ligne avec vos photos ou documents. Corrigez le nom, la quantité et le montant. Puis appuyez sur J’ai vérifié.":
        "Compare each row with your photos or documents. Fix the item name, quantity and amount. Then press I’ve checked.",
    # Factures
    "Validation humaine": "Human validation",
    "Vérifiez vos factures": "Review your invoices",
    "Les valeurs détectées sont des propositions. Corrigez-les si nécessaire, puis confirmez-les.":
        "Detected values are suggestions. Correct them if needed, then confirm.",
    "Facture": "Invoice",
    "Fournisseur": "Supplier",
    "Numéro de facture": "Invoice number",
    "Total hors taxes": "Net total",
    "Montant de TVA": "VAT amount",
    "Taux de TVA (%)": "VAT rate (%)",
    "Total à payer": "Total due",
    "Devise": "Currency",
    "Ex. MAD": "e.g. MAD",
    "J’ai vérifié ces informations": "I’ve checked this information",
    "Confirmation…": "Confirming…",
    # Traitement et erreurs
    "Préparation sécurisée": "Secure processing",
    "L’analyse a besoin de votre aide": "The analysis needs your help",
    "Analyse de votre fichier en cours": "Analyzing your file",
    "Vous pouvez laisser cette page ouverte. Le fichier original reste intact.": "You can keep this page open. The original file stays intact.",
    "Analyser maintenant sur cet ordinateur": "Analyze now on this computer",
    "Analyse locale…": "Local analysis…",
    "Revenir à l’accueil": "Back to home",
    "Réessayer": "Try again",
    "DataPilot reste disponible": "DataPilot is still available",
    "Session expirée": "Session expired",
    "Analyse introuvable": "Analysis not found",
    "Impossible de terminer cette action": "This action couldn’t be completed",
    "Votre session a expiré. Rechargez la page puis réessayez.": "Your session has expired. Reload the page and try again.",
    "Cette analyse n’existe plus ou n’est pas accessible.": "This analysis no longer exists or is not accessible.",
    "Une erreur inattendue s’est produite. Vos fichiers sont restés intacts.": "An unexpected error occurred. Your files are intact.",
    "Accès depuis votre téléphone": "Access from your phone",
    "Entrez votre code d’accès": "Enter your access code",
    "Le code est défini sur l’ordinateur qui héberge DataPilot.": "The code is set on the computer hosting DataPilot.",
    "Code d’accès": "Access code",
    "Entrer": "Enter",
    # Messages flash et erreurs serveur
    "Démonstration prête : explorez les résultats ci-dessous.": "Demo ready: explore the results below.",
    "Le fichier de démonstration est indisponible.": "The demo file is unavailable.",
    "La démonstration n’a pas pu être préparée.": "The demo couldn’t be prepared.",
    "Choisissez un fichier CSV, Excel ou JSON.": "Choose a CSV, Excel or JSON file.",
    "Format refusé. Utilisez CSV, XLSX, XLS ou JSON.": "Format rejected. Use CSV, XLSX, XLS or JSON.",
    "Le fichier n’a pas pu être traité.": "The file couldn’t be processed.",
    "Fichier reçu. L’analyse continue en arrière-plan.": "File received. Analysis continues in the background.",
    "Fichier importé et analysé.": "File imported and analyzed.",
    "Choisissez entre 1 et 8 documents.": "Choose between 1 and 8 documents.",
    "Un document n’a pas pu être préparé. Vérifiez son format et sa taille (20 Mo maximum par fichier).":
        "A document couldn’t be prepared. Check its format and size (20 MB max per file).",
    "Ajoutez au moins une ligne avec un article et un montant.": "Add at least one row with an item and an amount.",
    "Choisissez au moins une facture PDF ou une photo.": "Choose at least one PDF invoice or photo.",
    "Sélectionnez au maximum 10 factures à la fois.": "Select at most 10 invoices at a time.",
    "Formats acceptés : PDF, PNG, JPG, TIFF ou WEBP.": "Accepted formats: PDF, PNG, JPG, TIFF or WEBP.",
    "Ces factures n’ont pas pu être préparées. Essayez un autre document.": "These invoices couldn’t be prepared. Try another document.",
    "Les factures sont confirmées et prêtes pour les graphiques ou l’export.": "Invoices confirmed and ready for charts or export.",
    "L’analyse locale n’a pas pu aboutir. Vérifiez le fichier puis réessayez.": "Local analysis failed. Check the file and try again.",
    "Analyse terminée sur cet ordinateur.": "Analysis completed on this computer.",
    "Sélectionnez au moins une correction à appliquer.": "Select at least one fix to apply.",
    "Ces corrections ne sont plus nécessaires pour ce fichier.": "These fixes are no longer needed for this file.",
    "Retour au fichier d’origine.": "Back to the original file.",
    "Aucune étape récente ne peut être annulée.": "No recent step can be undone.",
    "La copie nécessaire à cette annulation est indisponible.": "The copy required for this undo is unavailable.",
    "La dernière étape de correction a été annulée.": "The last cleaning step was undone.",
    "Appliquez d’abord une correction avant de créer un modèle.": "Apply a fix before creating a recipe.",
    "Votre modèle de correction est prêt pour les prochains fichiers.": "Your cleaning recipe is ready for future files.",
    "Ce modèle ne contient aucune correction utile pour ce fichier.": "This recipe contains no useful fix for this file.",
    "L’objectif doit être un nombre, par exemple 1000 ou 1000,50.": "The target must be a number, for example 1000 or 1000.50.",
    "Ce chiffre apparaît maintenant dans votre résumé.": "This metric now appears in your summary.",
    "Le chiffre a été retiré du résumé.": "The metric was removed from the summary.",
    "Votre graphique a été créé et enregistré sur cet ordinateur.": "Your chart was created and saved on this computer.",
    "Le graphique a été supprimé.": "The chart was deleted.",
    "Code incorrect. Réessayez.": "Incorrect code. Try again.",
    "Le fichier dépasse la limite de 20 Mo.": "The file exceeds the 20 MB limit.",
    "Pipeline relancé : couches et contrôles à jour.": "Pipeline re-run: layers and checks are up to date.",
    "Le pipeline n’a pas pu être exécuté.": "The pipeline couldn’t run.",
    "Écrivez une requête SELECT.": "Write a SELECT query.",
    "La requête dépasse 4 000 caractères.": "The query exceeds 4,000 characters.",
    "Une seule requête à la fois.": "One query at a time.",
    "Seules les requêtes SELECT (ou WITH … SELECT) sont autorisées.": "Only SELECT (or WITH … SELECT) queries are allowed.",
    "Cette requête utilise une instruction non autorisée en lecture seule.": "This query uses a statement that isn’t allowed in read-only mode.",
    "Aucune table n'est encore publiée pour ce projet.": "No table has been published for this project yet.",
    "Ce nom personnalisé a été supprimé.": "This custom name was removed.",
    "Écrivez une question de 500 caractères maximum.": "Write a question of 500 characters maximum.",
    "Ce niveau d’explication n’est pas disponible.": "This explanation level is not available.",
    # Moteur : suggestions de nettoyage
    "Supprimer les doublons exacts": "Remove exact duplicates",
    "Conserve la première occurrence de chaque ligne identique.": "Keeps the first occurrence of each identical row.",
    "Convertir les cellules vides": "Convert blank cells",
    "Transforme les chaînes vides ou composées d'espaces en valeurs manquantes.": "Turns empty or whitespace-only strings into missing values.",
    "Retirer les espaces inutiles": "Trim extra spaces",
    "Nettoie les espaces au début et à la fin des textes.": "Removes leading and trailing spaces in text.",
    "Réparer les montants incohérents": "Repair inconsistent amounts",
    "Corrige les quantités, prix, remises et totaux contradictoires à partir des autres valeurs de la vente.":
        "Fixes contradictory quantities, prices, discounts and totals using the sale’s other values.",
    "Méthode robuste aux valeurs extrêmes ; chaque remplacement reste journalisé.": "Robust to outliers; every replacement is logged.",
    "Applique une valeur centrale après la conversion des nombres.": "Applies a central value after number conversion.",
    "Utilise le mode de la colonne et conserve cette décision dans le journal.": "Uses the column’s mode and records the decision in the log.",
    # Moteur : tableau de bord et synthèse
    "Nombre de lignes ": "Number of rows ",
    "Non renseigné": "Not provided",
    "Ce calcul montre une différence dans le fichier ; il ne prouve pas sa cause.": "This calculation shows a difference in the file; it doesn’t prove its cause.",
    "Meilleure contribution observée": "Top observed contributor",
    "Total observé": "Observed total",
    "Ventes déclarées": "Reported sales",
    "Dépenses déclarées": "Reported expenses",
    "Le total des montants n’est pas un bénéfice et ne prouve pas qu'il s'agit de ventes encaissées.":
        "The total amount is not a profit and doesn’t prove the sales were collected.",
    "Aucun coût complet n’a été identifié : la rentabilité ne peut pas être calculée.":
        "No full cost was identified: profitability can’t be calculated.",
    "Le total des dépenses ne donne ni bénéfice ni solde de trésorerie sans données de ventes.":
        "Total expenses give neither profit nor cash balance without sales data.",
    "Les montants négatifs sont inclus dans le total et demandent une vérification métier.":
        "Negative amounts are included in the total and need a business review.",
    "Comparer ce total à une période précédente de même durée avant de changer vos décisions.":
        "Compare this total with a previous period of the same length before changing your decisions.",
    "Ajoutez une colonne de montant des ventes pour obtenir une synthèse métier.": "Add a sales amount column to get a business summary.",
    "Aucun montant exploitable n’a été reconnu dans ce fichier.": "No usable amount was found in this file.",
    "Ces factures décrivent des achats ou des dépenses ; elles ne permettent pas de calculer les ventes.":
        "These invoices describe purchases or expenses; they can’t be used to calculate sales.",
    # Moteur : contrôles
    "Aucune ligne en double": "No duplicate rows",
    "Valeur obligatoire": "Required value",
    "Format numérique": "Numeric format",
    "Format de date": "Date format",
    "Date plausible": "Plausible date",
    "Identifiant unique": "Unique identifier",
    "Libellés cohérents": "Consistent labels",
    "Colonne présente": "Column present",
    "Aucune variante": "No variant",
    # Assistant local
    "Écrivez une question sur vos données.": "Write a question about your data.",
    "Aucune valeur manquante n’est détectée.": "No missing value detected.",
    "Comparaison de toutes les colonnes": "Comparison of all columns",
    "Comptage des valeurs nulles": "Null value count",
    "Comptage direct des lignes": "Direct row count",
    "Résumé calculé localement à partir du profil du fichier": "Summary computed locally from the file profile",
    "Quelles valeurs sont manquantes ?": "Which values are missing?",
    "Combien de doublons contient le fichier ?": "How many duplicates does the file contain?",
    "Combien de lignes contient le fichier ?": "How many rows does the file contain?",
    "Que dois-je vérifier ?": "What should I check?",
    "Aucune anomalie évidente n’est détectée, mais le sens métier des colonnes reste à confirmer.":
        "No obvious anomaly detected, but the business meaning of the columns still needs confirming.",
    "Les contrôles portent sur les cellules vides, doublons et colonnes sans variation.":
        "Checks cover empty cells, duplicates and columns without variation.",
    "Un fichier techniquement propre peut encore contenir des valeurs incorrectes sur le plan métier.":
        "A technically clean file can still contain values that are wrong from a business standpoint.",
    "Aucun problème structurel majeur n’est détecté. Vérifiez surtout que les libellés et unités correspondent bien à votre activité.":
        "No major structural problem detected. Mainly check that labels and units match your business.",
    "Je ne peux pas encore répondre de façon fiable à cette question.": "I can’t answer this question reliably yet.",
    "Top 5 par fréquence": "Top 5 by frequency",
    "Maximum calculé sur les valeurs numériques": "Maximum computed on numeric values",
    "Minimum calculé sur les valeurs numériques": "Minimum computed on numeric values",
    "L’explication détaillée n’est pas encore disponible": "The detailed explanation is not available yet",
    "Vérifiez sa marge avant d’en augmenter la promotion.": "Check its margin before increasing its promotion.",
    "Comparez les coûts et la période avant de réallouer des moyens.": "Compare costs and period before reallocating resources.",
    "Vérifiez si cette dépense peut être réduite sans nuire à l'activité.": "Check whether this expense can be reduced without hurting the business.",
    "Télécharger Excel": "Download Excel",
    "Le fichier original reste intact.": "The original file stays intact.",
    "Excellent": "Excellent",
    "Comptoir Atlas · ventes": "Comptoir Atlas · sales",
    "Mes ventes": "My sales",
    "Tendance mensuelle ": "Monthly trend ",
    "À surveiller": "Needs attention",
    "À corriger": "Needs fixing",
    "sur 100": "out of 100",
    "Voir les valeurs du graphique": "Show chart values",
    "Ajout en cours…": "Adding…",
    "Traitement en cours…": "Processing…",
    "Analyse en cours": "Analyzing",
    "Ajouter à mes graphiques": "Add to my charts",
    "Ajouté à mes graphiques": "Added to my charts",
    "La réponse est indisponible.": "The answer is unavailable.",
    "DataPilot prépare votre réponse": "DataPilot is preparing your answer",
    "La réponse prend trop de temps. Réessayez dans un instant.": "The answer is taking too long. Try again in a moment.",
    "La connexion avec DataPilot a été interrompue. Votre page et votre fichier sont restés intacts.": "The connection to DataPilot was interrupted. Your page and file are intact.",
    "Le graphique n’a pas pu être ajouté.": "The chart couldn’t be added.",
    "Le fichier dépasse la taille acceptée.": "The file exceeds the accepted size.",
    "La réponse reçue est incomplète. Rechargez la page puis réessayez.": "The response is incomplete. Reload the page and try again.",
    "La réponse reçue est illisible. Rechargez la page puis réessayez.": "The response is unreadable. Reload the page and try again.",
    "L’affichage n’a pas pu être actualisé.": "The display couldn’t be refreshed.",
    "Les nouvelles informations n’ont pas pu être affichées.": "The new information couldn’t be displayed.",
    "La modification n’a pas pu être appliquée.": "The change couldn’t be applied.",
    "La modification a été enregistrée, mais l’affichage n’a pas pu être actualisé.": "The change was saved, but the display couldn’t be refreshed.",
    "La connexion a été interrompue. Vos données sont restées intactes.": "The connection was interrupted. Your data is intact.",
    "DataPilot a reçu une réponse inattendue. Réessayez dans un instant.": "DataPilot received an unexpected response. Try again in a moment.",
    "Le suivi est momentanément indisponible. Vous pouvez lancer l’analyse locale ci-dessous.": "Progress tracking is temporarily unavailable. You can start the local analysis below.",
    "Ce graphique ne contient pas assez de valeurs fiables pour être affiché.": "This chart doesn’t contain enough reliable values to display.",
    "Le module de graphiques est indisponible pour le moment.": "The chart module is unavailable right now.",
    "Les données de ce graphique sont temporairement illisibles.": "This chart’s data is temporarily unreadable.",
    "Le graphique doit être affiché avant de pouvoir être téléchargé.": "The chart must be displayed before it can be downloaded.",
    "Le téléchargement de ce graphique est indisponible pour le moment.": "Downloading this chart is unavailable right now.",
    "Nombre de lignes": "Number of rows",
    "Analyse placée dans la file d’attente…": "Analysis queued…",
    "Lecture et vérification du fichier…": "Reading and checking the file…",
    "Préparation de l’analyse…": "Preparing the analysis…",
    "Analyse prête.": "Analysis ready.",
    "Colonne de montant non reconnue : ajoutez les lignes manuellement.": "Amount column not recognized: add the rows manually.",
    "Autorisez l’envoi du titre choisi ou utilisez la saisie manuelle.": "Allow sending the chosen title or use manual entry.",
    "Source": "Source",
    "À compléter": "To complete",
    "Confirmé par vous": "Confirmed by you",
    "Atteint": "Reached",
    "En dessous de l’objectif": "Below target",
    "Au-dessus de l’objectif": "Above target",
    "Suivi": "Tracked",
}


# ---------------------------------------------------------------------------
# Motifs pour les phrases générées
# ---------------------------------------------------------------------------

def _kind(match_text: str) -> str:
    return {"nombre": "number", "catégorie": "category", "date": "date", "texte": "text",
            "booléen": "boolean", "jour": "day", "semaine": "week", "mois": "month"}.get(match_text, match_text)


def _plural(count: str, singular: str, plural: str) -> str:
    return f"{count} {singular if count.strip() in {'0', '1'} else plural}"


PATTERNS_EN: list[tuple[str, str | Callable[[re.Match], str]]] = [
    # Nettoyage
    (r"Harmoniser les libellés de (.+)", r"Harmonize labels in \1"),
    (r"Regroupe (\d+) saisie\(s\) proche\(s\) sous des libellés déjà présents, par exemple : (.+)\.",
     r"Groups \1 near-duplicate entries under existing labels, for example: \2."),
    (r"Compléter (.+) par la médiane", r"Fill \1 with the median"),
    (r"Compléter (.+) par la valeur fréquente", r"Fill \1 with the most frequent value"),
    (r"Convertir (.+) en nombre", r"Convert \1 to number"),
    (r"Convertir (.+) en date", r"Convert \1 to date"),
    (r"(\d+) % des valeurs reconnues comme numériques\.", r"\1% of values recognized as numbers."),
    (r"(\d+) % des valeurs reconnues comme dates\.", r"\1% of values recognized as dates."),
    (r"(\d+) lignes? concernées?", r"\1 rows affected"),
    (r"(\d+) correction(s?) appliquée(s?)\. Vous pouvez annuler cette étape\.",
     lambda m: f"{_plural(m.group(1), 'fix', 'fixes')} applied. You can undo this step."),
    (r"Le modèle a appliqué (\d+) corrections?\.", lambda m: f"The recipe applied {_plural(m.group(1), 'fix', 'fixes')}."),
    (r"(\d+) étapes? enregistrées?", lambda m: _plural(m.group(1), "saved step", "saved steps")),
    (r"(\d+) corrections? prévues?", lambda m: _plural(m.group(1), "planned fix", "planned fixes")),
    (r"Modèle : (.+)", r"Recipe: \1"),
    # Tableau de bord
    (r"Évolution des lignes · (.+)", r"Rows over time · \1"),
    (r"Total (.+) par (.+)", r"Total \1 by \2"),
    (r"Table Gold · (.+)", r"Gold table · \1"),
    (r"Regroupement par (jour|semaine|mois) · 24 périodes récentes maximum",
     lambda m: f"Grouped by {_kind(m.group(1))} · last 24 periods max"),
    (r"Répartition des valeurs · (.+)", r"Value distribution · \1"),
    (r"Distribution · (.+)", r"Distribution · \1"),
    (r"Répartition · (.+)", r"Breakdown · \1"),
    (r"(Nombre|Somme|Moyenne|Médiane) de (.+) par (.+)", lambda m: f"{ {'Nombre': 'Count', 'Somme': 'Sum', 'Moyenne': 'Average', 'Médiane': 'Median'}[m.group(1)] } of {m.group(2)} by {m.group(3)}"),
    (r"(.+) · (nombre|catégorie|date|texte)", lambda m: f"{m.group(1)} · {_kind(m.group(2))}"),
    (r"Le point le plus élevé est « (.+) » avec (.+)\.", r"The highest point is “\1” at \2."),
    (r"Il se situe à ([\d.,]+) % au-dessus de la moyenne affichée\.", r"It is \1% above the displayed average."),
    (r"Il se situe à ([\d.,]+) % en dessous de la moyenne affichée\.", r"It is \1% below the displayed average."),
    (r"Par rapport au point précédent, la différence est de (.+)\.", r"Compared with the previous point, the difference is \1."),
    (r"Ce point représente ([\d.,]+) % du total positif affiché\.", r"This point represents \1% of the displayed positive total."),
    # Synthèse métier
    (r"(.+) représente ([\d.,]+) % des montants positifs selon « (.+) »\. (.+)",
     lambda m: f"{m.group(1)} accounts for {m.group(2)}% of positive amounts by “{m.group(3)}”. {gettext(m.group(4), 'en')}"),
    (r"Vérifier la disponibilité et la marge de « (.+) » avant de renforcer sa mise en avant\.",
     r"Check the availability and margin of “\1” before promoting it further."),
    (r"Comparer les ventes et les coûts de « (.+) » avec les autres villes avant de réallouer des moyens\.",
     r"Compare sales and costs of “\1” with other cities before reallocating resources."),
    (r"Examiner les justificatifs de « (.+) » et chercher une économie possible\.", r"Review the receipts for “\1” and look for possible savings."),
    (r"Vérifier (\d+) ligne\(s\) sans montant exploitable avant de tirer une conclusion\.", r"Check \1 row(s) without a usable amount before drawing conclusions."),
    (r"Contrôler (\d+) montant\(s\) négatif\(s\) : retour, remboursement ou erreur de saisie \?", r"Check \1 negative amount(s): return, refund or data entry error?"),
    (r"(\d+) ligne\(s\) exclue\(s\) du total faute de montant valide\.", r"\1 row(s) excluded from the total for lack of a valid amount."),
    (r"(\d+) ligne\(s\) identique\(s\) sont incluses dans le total ; vérifiez s’il s’agit de doublons réels\.",
     r"\1 identical row(s) are included in the total; check whether they are real duplicates."),
    (r"Somme de la colonne « (.+) » ; (\d+) ligne\(s\) sans montant exploitable exclue\(s\)\.",
     r"Sum of column “\1”; \2 row(s) without a usable amount excluded."),
    # Contrôles du contrat
    (r"Valeur ≥ (.+)", r"Value ≥ \1"),
    (r"Valeur ≤ (.+)", r"Value ≤ \1"),
    (r"(\d+) doublon\(s\) exact\(s\)", r"\1 exact duplicate(s)"),
    (r"(\d+) cellule\(s\) vide\(s\)", r"\1 empty cell(s)"),
    (r"(\d+) valeur\(s\) non numérique\(s\)", r"\1 non-numeric value(s)"),
    (r"(\d+) valeur\(s\) négative\(s\)", r"\1 negative value(s)"),
    (r"(\d+) valeur\(s\) hors échelle", r"\1 out-of-scale value(s)"),
    (r"(\d+) date\(s\) illisible\(s\)", r"\1 unreadable date(s)"),
    (r"(\d+) date\(s\) future\(s\), (\d+) avant 2000", r"\1 future date(s), \2 before 2000"),
    (r"(\d+) identifiant\(s\) répété\(s\)", r"\1 repeated identifier(s)"),
    # Flash et divers
    (r"(\d+) lignes? vérifiées? : votre analyse est prête\.", lambda m: f"{_plural(m.group(1), 'row', 'rows')} verified: your analysis is ready."),
    (r"(\d+) factures? préparées?\. Vérifiez maintenant les informations détectées\.",
     lambda m: f"{_plural(m.group(1), 'invoice', 'invoices')} prepared. Now review the detected information."),
    (r"Ligne (\d+) : (.+)", lambda m: f"Row {m.group(1)}: {gettext(m.group(2), 'en')}"),
    (r"(\d+) graphiques? enregistrés?", lambda m: _plural(m.group(1), "saved chart", "saved charts")),
    (r"(\d+) valeurs? renommées?", lambda m: _plural(m.group(1), "renamed value", "renamed values")),
    (r"Erreur SQL : (.+)", r"SQL error: \1"),
    (r"(\d+) ligne\(s\) proposées depuis la photo par lecture locale\. Vérifiez chaque montant\.", r"\1 row(s) proposed from the photo by local OCR. Check every amount."),
    (r"(\d+) ligne\(s\) proposées depuis les pages scannées \(3 pages maximum\)\. Vérifiez-les\.", r"\1 row(s) proposed from the scanned pages (3 pages max). Review them."),
    (r"(\d+) ligne\(s\) proposées depuis le texte\. Vérifiez-les\.", r"\1 row(s) proposed from the text. Review them."),
    (r"(\d+) ligne\(s\) proposées depuis le tableau\. Vérifiez-les\.", r"\1 row(s) proposed from the table. Review them."),
    (r"Tableau non lu : (.+)", r"Table not read: \1"),
    (r"(\d+) ligne\(s\) proposée\(s\)", r"\1 proposed row(s)"),
    # Assistant local
    (r"Le fichier contient ([\d  ,]+) lignes\.", r"The file contains \1 rows."),
    (r"Le fichier contient (\d+) colonnes\.", r"The file contains \1 columns."),
    (r"J’ai trouvé (\d+) ligne\(s\) dupliquée\(s\) exactement\.", r"I found \1 exact duplicate row(s)."),
    (r"Colonnes les plus incomplètes : (.+)\.", r"Most incomplete columns: \1."),
    (r"Votre fichier contient ([\d  ,]+) lignes et (\d+) colonnes, avec une qualité globale de (\d+)/100\.",
     r"Your file contains \1 rows and \2 columns, with an overall quality of \3/100."),
    (r"J’ai détecté (\d+) cellule\(s\) vide\(s\) et (\d+) doublon\(s\)\.", r"I detected \1 empty cell(s) and \2 duplicate(s)."),
    (r"(.+) : moyenne (.+), de (.+) à (.+)\.", r"\1: average \2, from \3 to \4."),
    (r"Dans (.+), la valeur la plus fréquente est « (.+) » \((\d+) ligne\(s\)\)\.", r"In \1, the most frequent value is “\2” (\3 row(s))."),
    (r"Quelle est la moyenne de (.+) \?", r"What is the average of \1?"),
    (r"Quelle est la répartition de (.+) \?", r"What is the breakdown of \1?"),
    (r"Quelles sont les principales valeurs de (.+) \?", r"What are the main values of \1?"),
    (r"(\d+) cellule\(s\) vide\(s\) peuvent limiter certaines analyses\.", r"\1 empty cell(s) may limit some analyses."),
    (r"(\d+) doublon\(s\) devraient être vérifiés\.", r"\1 duplicate(s) should be checked."),
    (r"À vérifier en priorité : (.+)\.", lambda m: "Check first: " + re.sub(r"(\d+) cellule\(s\) vide\(s\)", r"\1 empty cell(s)", re.sub(r"(\d+) doublon\(s\)", r"\1 duplicate(s)", m.group(1))).replace("colonne(s) sans variation", "column(s) without variation") + "."),
    (r"Score qualité calculé localement : (\d+)/100", r"Quality score computed locally: \1/100"),
    (r"La (moyenne|somme) de (.+) est (.+)\.", lambda m: f"The {'average' if m.group(1) == 'moyenne' else 'sum'} of {m.group(2)} is {m.group(3)}."),
    (r"Le (maximum|minimum) de (.+) est (.+)\.", r"The \1 of \2 is \3."),
    (r"Principales valeurs de (.+) — (.+)\.", r"Main values of \1 — \2."),
    (r"(\d+) valeurs numériques utilisées", r"\1 numeric values used"),
    (r"Comptage des valeurs distinctes de « (.+) »", r"Count of distinct values in “\1”"),
    (r"Méthode · (.+)", lambda m: f"Method · {gettext(m.group(1), 'en')}"),
]

_COMPILED_PATTERNS = [(re.compile(pattern), replacement) for pattern, replacement in PATTERNS_EN]


# Reformulation des questions anglaises pour l'assistant local à règles françaises.
QUESTION_EN_TO_FR: list[tuple[str, str]] = [
    (r"how many rows|number of rows|row count", "combien de lignes"),
    (r"how many columns|number of columns", "combien de colonnes"),
    (r"duplicates?", "doublon"),
    (r"missing|empty|blank|null", "manquant"),
    (r"key takeaways?|summary|summari[sz]e|overview|what should i remember|main insights?", "a retenir"),
    (r"what should i check|check first|quality|issues?|problems?|reliable", "verifier"),
    (r"average|mean", "moyenne"),
    (r"\bsum\b|\btotal\b", "somme"),
    (r"highest|maximum|\bmax\b", "maximum"),
    (r"lowest|minimum|\bmin\b", "minimum"),
    (r"breakdown|distribution|top values?|most frequent", "repartition"),
    (r"suggest a (useful )?chart|chart", "graphique"),
    (r"\bby\b", "par"),
]
