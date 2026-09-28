"""Costruzione dello state e chiamata a Jev (TypeSafe System One)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from traffic_light_review import questions as q
from traffic_light_review.config import JevConfig
from traffic_light_review.diff import FileChange, file_patch
from traffic_light_review.globs import matches_any
from traffic_light_review.redact import redact

STATUS_NAMES = {"A": "added", "M": "modified", "D": "deleted"}
TRUNCATION_MARK = "\n[... diff truncated ...]\n"
# Limite dello state inviato a Jev, in token stimati come caratteri / CHARS_PER_TOKEN.
# Il modello regge 32k token per state più la domanda più lunga; le 18 domande pesano circa 5,4k (misurati con l'SDK).
# 3 caratteri per token: il codice tokenizza peggio della prosa, con 4 l'API risponde 400.
MAX_STATE_TOKENS = 24000
CHARS_PER_TOKEN = 3
# Caratteri massimi del diff di un singolo file. Oltre, il file viene troncato.
MAX_FILE_CHARS = 12000
# Prezzo dell'input di Jev in dollari per milione di token (l'output è gratuito), per il riepilogo e il log.
INPUT_USD_PER_MTOK = 0.042
# Sotto questo spazio residuo un file non entra nemmeno troncato: lo si omette.
MIN_PARTIAL_CHARS = 400
# File scartati per primi quando il diff non entra: generati, minificati, snapshot, asset.
LOW_PRIORITY_PATHS = (
    "*generated*", "*.gen.*", "*_pb2.py", "*_pb2_grpc.py", "*.pb.go", "*.min.*", "*.map",
    "*.snap", "*.svg", "*.lock", "*-lock.*", "*.lock.json",
)
TASK = (
    "Triage of the code changed in one turn by an AI coding assistant, to decide whether it needs a code review. "
    "`changed_files` lists every changed file with its status and lines; its `diff` field says whether the file "
    "is in `diff` in full, truncated or omitted for size. `diff` is the unified diff of the turn, code and tests "
    "together. `omitted_files` counts the files left out of `diff`. `[REDACTED]` replaces secret values removed "
    "before sending; any prefix right before it is original."
)
# L'unica fonte della API key: niente config, niente portachiavi, niente file.
API_KEY_ENV = "TYPESAFE_API_KEY"
MISSING_KEY_MESSAGE = (
    f"{API_KEY_ENV} non impostata. Esportala nella shell da cui avvii Claude Code "
    f"(export {API_KEY_ENV}=..., per esempio in ~/.zshrc) e riavvia la sessione."
)


class JevUnavailable(Exception):
    """Jev non chiamabile (rete, API). Il messaggio è sicuro da mostrare."""


class JevKeyMissing(Exception):
    """TYPESAFE_API_KEY assente o vuota: l'hook esce con un codice dedicato invece di dare un verdetto."""


@dataclass
class JevAnswers:
    nouls: Dict[str, float]  # nome → probabilità del sì
    scores: Dict[str, float]  # nome → valore atteso 0..n-1 (media dei livelli pesata, non intera)
    score_confidence: Dict[str, float]
    concern: str  # opzione di primary_concern
    concern_confidence: float
    concern_probabilities: Dict[str, float] = field(default_factory=dict)
    usage: Dict[str, int] = field(default_factory=dict)

    @property
    def values(self) -> Dict[str, float]:
        """Valori su cui girano le regole della policy."""
        return {**self.nouls, **self.scores}


def critical_files(counted: List[FileChange], file_hunks: Dict[str, str]) -> Dict[str, List[str]]:
    """Per ogni check critico, i file che corrispondono ai suoi escalation_patterns (percorso o contenuto)."""
    result = {}
    for name in q.CRITICAL:
        check = q.CHECKS[name]
        result[name] = [f.path for f in counted if check.matches_file(f.path, file_hunks.get(f.path, ""))]
    return result


def _priority(f: FileChange, relevant: set) -> tuple:
    # Per ultimi generati e asset, anche se il nome sembra rilevante (api_pb2.py); prima i file rilevanti
    # per un check critico, poi gli altri. A parità, i file con più righe cambiate.
    tier = 2 if matches_any(f.path, LOW_PRIORITY_PATHS) else 0 if f.path in relevant else 1
    return tier, -((f.added or 0) + (f.deleted or 0))


def _encoded_len(text: str) -> int:
    """Lunghezza del testo dentro il JSON inviato (\\n, virgolette e non-ASCII escapati)."""
    return len(json.dumps(text)) - 2


def _truncate(patch: str, limit: int) -> str:
    """Taglia la patch perché, codificata, stia in limit caratteri. Stringa vuota se non ci sta niente."""
    # La lunghezza codificata cresce con il taglio: si cerca il taglio più lungo che ci sta.
    lo, hi = 0, min(len(patch), limit)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _encoded_len(patch[:mid] + TRUNCATION_MARK) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return patch[:lo] + TRUNCATION_MARK if lo else ""


def build_state(
    counted: List[FileChange], file_hunks: Optional[Dict[str, str]],
    relevant: Optional[Dict[str, List[str]]] = None,
) -> dict:
    """State per Jev: elenco dei file e diff unificato, oscurato e tagliato a MAX_STATE_TOKENS.

    Se il diff non entra si tolgono per primi i file meno rilevanti; il numero di file omessi va nello state,
    così né Jev né l'utente scambiano un diff parziale per quello intero.
    relevant è il risultato di critical_files(), se il chiamante l'ha già calcolato.
    """
    # Senza hunk (hunks() non ha potuto abbinarli a numstat) i file di testo vanno segnati come omessi:
    # una patch con la sola intestazione sembrerebbe una modifica vuota.
    available = file_hunks is not None
    file_hunks = file_hunks or {}
    if relevant is None:
        relevant = critical_files(counted, file_hunks)
    priority = {p for paths in relevant.values() for p in paths}
    ordered = sorted(counted, key=lambda f: _priority(f, priority))
    entries = {
        f.path: {"path": f.path, "status": STATUS_NAMES[f.status], "lines_added": f.added,
                 "lines_deleted": f.deleted, "diff": "truncated"}
        for f in ordered
    }
    state = {"task": TASK, "changed_files": list(entries.values()), "omitted_files": 0, "truncated_files": 0,
             "secrets_redacted": 0, "diff": ""}
    # Margine per i contatori, che crescono di qualche cifra quando vengono riempiti.
    budget = int(MAX_STATE_TOKENS * CHARS_PER_TOKEN) - len(json.dumps(state)) - 50

    parts, redacted = [], 0
    for f in ordered:
        entry = entries[f.path]
        if not available and f.added is not None:
            entry["diff"] = "omitted"
            continue
        body, n = redact(file_hunks.get(f.path, ""))
        patch = file_patch(f, body)
        limit = min(MAX_FILE_CHARS, budget)
        if _encoded_len(patch) <= limit:
            entry["diff"] = "binary" if f.added is None else "full"
        elif limit >= MIN_PARTIAL_CHARS and (cut := _truncate(patch, limit)):
            patch = cut
        else:
            entry["diff"] = "omitted"
            continue
        redacted += n
        parts.append(patch)
        budget -= _encoded_len(patch)

    state["diff"] = "".join(parts)
    state["omitted_files"] = sum(e["diff"] == "omitted" for e in entries.values())
    state["truncated_files"] = sum(e["diff"] == "truncated" for e in entries.values())
    state["secrets_redacted"] = redacted
    return state


def build_questions() -> dict:
    """Le domande di checks.json, con instructions come oggetto {question, untrusted_input}."""
    from typesafe_sdk import Choice, Noul, Score

    kinds = {"noul": Noul, "score": Score, "choice": Choice}
    return {
        name: kinds[c.type](
            instructions={"question": c.instructions, "untrusted_input": q.UNTRUSTED_INPUT},
            criteria=c.criteria,
        )
        for name, c in q.CHECKS.items()
    }


def ask(state: dict, cfg: JevConfig) -> JevAnswers:
    """Una sola richiesta con tutte le domande: Jev le valuta in parallelo."""
    api_key = os.environ.get(API_KEY_ENV, "").strip()
    if not api_key:
        raise JevKeyMissing(MISSING_KEY_MESSAGE)

    from typesafe_sdk import RetryPolicy, TypeSafeAPIError, TypeSafeClient, TypeSafeError

    try:
        with TypeSafeClient(
            api_key=api_key,
            model=cfg.model,
            retry=RetryPolicy(max_retries=1, timeout=cfg.timeout_s),
            timeout=cfg.timeout_s,
        ) as client:
            resp = client.system_one(state=state, questions=build_questions())
    except TypeSafeAPIError as exc:
        # Solo tipo e status HTTP: il messaggio potrebbe riportare dettagli della richiesta.
        status = getattr(exc, "status", None)
        raise JevUnavailable(f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")) from None
    except TypeSafeError as exc:
        raise JevUnavailable(type(exc).__name__) from None

    try:
        concern = resp.choices[q.CHOICE]
        usage = {}
        if resp.usage is not None:
            usage = {"input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens}
        return JevAnswers(
            nouls={name: float(resp.nouls[name].noul) for name in q.NOULS},
            scores={name: float(resp.scores[name].score) for name in q.SCORES},
            score_confidence={name: float(resp.scores[name].confidence) for name in q.SCORES},
            concern=concern.choice,
            concern_confidence=float(concern.confidence),
            concern_probabilities={k: float(v) for k, v in dict(concern.probabilities).items()},
            usage=usage,
        )
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        # Risposta senza una delle domande o con un campo inatteso: solo il tipo, come per gli errori HTTP.
        raise JevUnavailable(f"risposta incompleta ({type(exc).__name__})") from None


def cost_usd(answers: JevAnswers) -> float:
    """Costo della richiesta: si paga solo l'input."""
    return answers.usage.get("input_tokens", 0) * INPUT_USD_PER_MTOK / 1_000_000
