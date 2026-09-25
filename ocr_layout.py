# -*- coding: utf-8 -*-
"""Reconstruction des lignes lues par l'OCR local (RapidOCR).

L'OCR renvoie des blocs de texte avec leur quadrilatère. Sur une photo inclinée,
le montant écrit à droite peut apparaître avant l'article écrit à gauche dans
l'ordre de lecture brut, ce qui décale tous les montants d'une ligne. On estime
donc l'inclinaison à partir des blocs eux-mêmes, on redresse leurs centres, puis
on regroupe les blocs qui partagent la même ligne avant de les lire de gauche à
droite.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any


@lru_cache(maxsize=1)
def _engine():
    from rapidocr import RapidOCR

    return RapidOCR()


def _box_geometry(box: Any) -> tuple[float, float, float, float, float]:
    points = [(float(x), float(y)) for x, y in box]
    (x0, y0), (x1, y1), (_x2, _y2), (x3, y3) = points
    width = math.hypot(x1 - x0, y1 - y0)
    height = math.hypot(x3 - x0, y3 - y0)
    angle = math.atan2(y1 - y0, x1 - x0) if width > 1 else 0.0
    cx = sum(x for x, _ in points) / 4
    cy = sum(y for _, y in points) / 4
    return cx, cy, width, height, angle


def group_lines(boxes: list, texts: list[str], scores: list[float]) -> list[dict[str, Any]]:
    """Regroupe les blocs OCR en lignes logiques, en tenant compte de l'inclinaison."""
    items = []
    for box, text, score in zip(boxes, texts, scores):
        if not str(text).strip():
            continue
        cx, cy, width, height, angle = _box_geometry(box)
        items.append({"cx": cx, "cy": cy, "w": width, "h": max(height, 1.0), "angle": angle,
                      "text": str(text).strip(), "score": float(score)})
    if not items:
        return []
    wide = [item["angle"] for item in items if item["w"] >= 2 * item["h"]]
    theta = median(wide) if wide else 0.0
    cos_t, sin_t = math.cos(-theta), math.sin(-theta)
    for item in items:
        item["x"] = item["cx"] * cos_t - item["cy"] * sin_t
        item["y"] = item["cx"] * sin_t + item["cy"] * cos_t
    typical_height = median(item["h"] for item in items)
    rows: list[list[dict[str, Any]]] = []
    for item in sorted(items, key=lambda value: value["y"]):
        if rows:
            row_y = sum(member["y"] for member in rows[-1]) / len(rows[-1])
            if abs(item["y"] - row_y) <= 0.55 * typical_height:
                rows[-1].append(item)
                continue
        rows.append([item])
    lines = []
    for row in rows:
        ordered = sorted(row, key=lambda value: value["x"])
        lines.append({
            "text": " ".join(member["text"] for member in ordered),
            "score": min(member["score"] for member in ordered),
        })
    return lines


def read_lines(path: Path) -> dict[str, Any]:
    """Lit une image localement et renvoie les lignes reconstruites et l'angle estimé."""
    result = _engine()(str(path))
    boxes = list(result.boxes) if getattr(result, "boxes", None) is not None else []
    texts = list(result.txts or [])
    scores = list(result.scores or [1.0] * len(texts))
    if not boxes or len(boxes) != len(texts):
        return {"lines": [{"text": text, "score": float(score)} for text, score in zip(texts, scores)]}
    return {"lines": group_lines(boxes, texts, scores)}
