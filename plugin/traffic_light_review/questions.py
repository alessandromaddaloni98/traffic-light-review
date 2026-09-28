"""Domande per Jev, lette da checks.json. I testi sono in inglese: è la lingua in cui Jev è più accurato.

Nessuna dipendenza dall'SDK, così anche la config può validare nomi e livelli.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from traffic_light_review.globs import matches_any

CHECKS_PATH = Path(__file__).with_name("checks.json")


@dataclass(frozen=True)
class Check:
    name: str
    type: str  # noul | score | choice
    label: str  # etichetta italiana per i messaggi
    instructions: str
    criteria: object
    critical: bool = False
    higher_is_better: bool = False
    policy: bool = True  # False: la domanda non può comparire nelle regole (merge_ready)
    ocr_category: str = "other"
    ocr_categories: Optional[Dict[str, str]] = None  # solo choice: categoria OCR di ogni opzione
    label_low: Optional[str] = None  # etichetta quando scatta una regola lte su un check higher_is_better
    path_patterns: Tuple[str, ...] = ()
    content_patterns: Tuple[re.Pattern, ...] = ()

    @property
    def levels(self) -> int:
        """Numero di livelli di uno Score (valori 0..levels-1)."""
        return len(self.criteria) if self.type == "score" else 0

    def matches_file(self, path: str, text: str) -> bool:
        """True se il file è rilevante per il check secondo gli escalation_patterns (percorso o contenuto)."""
        return matches_any(path, self.path_patterns) or any(p.search(text) for p in self.content_patterns)


def _load() -> Tuple[str, Dict[str, Check]]:
    raw = json.loads(CHECKS_PATH.read_text(encoding="utf-8"))
    checks = {}
    for name, spec in raw.items():
        if name.startswith("_"):
            continue
        patterns = spec.get("escalation_patterns", {})
        checks[name] = Check(
            name=name,
            type=spec["type"],
            label=spec["label"],
            instructions=spec["instructions"],
            criteria=spec["criteria"],
            critical=spec.get("critical", False),
            higher_is_better=spec.get("higher_is_better", False),
            policy=spec.get("policy", True),
            ocr_category=spec.get("ocr_category", "other"),
            ocr_categories=spec.get("ocr_categories"),
            label_low=spec.get("label_low"),
            path_patterns=tuple(patterns.get("paths", ())),
            content_patterns=tuple(re.compile(p) for p in patterns.get("content", ())),
        )
    return raw["_untrusted_input"], checks


UNTRUSTED_INPUT, CHECKS = _load()

NOULS: List[str] = [n for n, c in CHECKS.items() if c.type == "noul"]
SCORES: List[str] = [n for n, c in CHECKS.items() if c.type == "score"]
CHOICE: str = next(n for n, c in CHECKS.items() if c.type == "choice")
# Check critici: solo domande sì/no, perché la banda di incertezza è una banda di probabilità.
CRITICAL: List[str] = [n for n, c in CHECKS.items() if c.critical and c.type == "noul"]
# Domande che le regole di decision.* possono usare.
POLICY_CHECKS: List[str] = [n for n, c in CHECKS.items() if c.type in ("noul", "score") and c.policy]
