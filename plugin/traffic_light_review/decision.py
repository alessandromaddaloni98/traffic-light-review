"""Decisione sulla review (sì o no): regole dichiarative della config applicate alle risposte di Jev.

Funzione pura: le soglie stanno tutte in decision.* della config, qui c'è solo come si combinano.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from traffic_light_review.config import DecisionConfig
from traffic_light_review.jev import JevAnswers
from traffic_light_review.questions import CHECKS, CHOICE, CRITICAL


@dataclass
class Hit:
    check: str
    value: float
    rule: str  # es. "hardcoded_secret ≥ 0.7"
    aggravating: bool  # regola aggravante: non fa proporre la review da sola


@dataclass
class Decision:
    review: bool  # True: Jev consiglia la review
    hits: List[Hit]  # regole scattate, prima quelle della review e poi le aggravanti
    uncertain: Dict[str, float]  # check critici nella banda di incertezza → probabilità
    band: tuple  # (uncertainty_low, uncertainty_high) della config, per i messaggi


def decide(answers: JevAnswers, cfg: DecisionConfig) -> Decision:
    values = answers.values
    hits = [
        Hit(check=r.when.check, value=values[r.when.check], rule=str(r.when), aggravating=aggravating)
        for aggravating, rules in ((False, cfg.review), (True, cfg.aggravating))
        for r in rules
        if r.fires(values)
    ]
    # Un check critico nella banda non è stato deciso da Jev: la banda lo registra ma non decide.
    # Compare tra i motivi e nel focus solo se la review parte per una regola di decision.review.
    uncertain = {
        name: answers.nouls[name] for name in CRITICAL
        if cfg.uncertainty_low <= answers.nouls[name] <= cfg.uncertainty_high
    }
    review = any(not h.aggravating for h in hits)
    return Decision(review=review, hits=hits, uncertain=uncertain, band=(cfg.uncertainty_low, cfg.uncertainty_high))


def fmt_value(name: str, value: float) -> str:
    check = CHECKS[name]
    # Per gli score Jev dà il valore atteso (es. 1.7), non un livello intero.
    return f"{value:.1f}/{check.levels - 1}" if check.type == "score" else f"{value:.2f}"


def _hit_text(hit: Hit) -> str:
    return f"{CHECKS[hit.check].label} {fmt_value(hit.check, hit.value)}"


def reasons(decision: Decision) -> List[str]:
    """Motivi della review in italiano: regole scattate, check incerti, aggravanti."""
    if not decision.review:
        return []
    top = [_hit_text(h) for h in decision.hits if not h.aggravating]
    uncertain = [f"{CHECKS[n].label} {p:.2f} (incerto)" for n, p in decision.uncertain.items()]
    worse = [f"{_hit_text(h)} (aggravante)" for h in decision.hits if h.aggravating]
    return top + uncertain + worse


def describe(decision: Decision) -> str:
    """Riga di verdetto, es. "🔴 Jev: review consigliata (credenziale nel codice 0.91)"."""
    if not decision.review:
        return "🟢 Jev: nessuna review necessaria"
    return f"🔴 Jev: review consigliata ({'; '.join(reasons(decision))})"


def uncertainty_line(decision: Decision) -> str:
    low, high = decision.band
    if not decision.uncertain:
        return f"Jev deciso su tutti i check critici (banda {low:g}-{high:g})"
    items = ", ".join(f"{name} {p:.2f}" for name, p in decision.uncertain.items())
    return f"Jev incerto su {items} (banda {low:g}-{high:g})"


def _light(name: str, value: float, band: tuple) -> str:
    """Colore di una risposta: 🔴 sopra la banda, 🟡 dentro, 🟢 sotto; al contrario se alto è buono."""
    check = CHECKS[name]
    if check.type == "score":
        value = value / max(check.levels - 1, 1)
    if check.higher_is_better:
        value = 1 - value
    return "🔴" if value > band[1] else "🟡" if value >= band[0] else "🟢"


def all_checks(answers: JevAnswers, decision: Decision) -> List[str]:
    """Una riga per ogni risposta di Jev; merge_ready (fuori dalla policy) in fondo, senza colore."""
    lines, outside = [], []
    for name, check in CHECKS.items():
        if name == CHOICE:
            continue
        value = answers.values[name]
        text = f"{name} {fmt_value(name, value)}  {check.label}"
        if not check.policy:
            outside.append(f"  ⚪ {text} (non usata dalla policy)")
        else:
            lines.append(f"  {_light(name, value, decision.band)} {text}")
    prob = answers.concern_probabilities.get(answers.concern, answers.concern_confidence)
    lines.append(f"  {CHOICE}: {answers.concern} {prob:.2f}")
    return lines + outside
