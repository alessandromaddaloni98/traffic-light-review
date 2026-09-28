"""Oscuramento dei segreti in chiaro negli hunk, prima di mandarli a Jev.

Solo il testo inviato a TypeSafe passa di qui: il diff per il subagent resta locale e intatto.
Nel dubbio si oscura: un falso positivo toglie a Jev poco contesto, un falso negativo fa uscire una chiave.
Restano in chiaro i prefissi riconoscibili (sk_live_, AKIA, ghp_…) e i placeholder (CHANGEME, <token>, ${VAR}):
non sono segreti, e senza di loro Jev non distingue una chiave vera da una finta.
"""

from __future__ import annotations

import re
from typing import Tuple

MARK = "[REDACTED]"

# Token con formato riconoscibile: il gruppo p (prefisso) resta, il resto si sostituisce.
_TOKENS = re.compile(
    "|".join([
        r"\b(?P<p1>AKIA|ASIA)[0-9A-Z]{16}\b",  # AWS access key id
        r"\b(?P<p2>sk-(?:[a-z]{2,8}-)?)[A-Za-z0-9_\-]{20,}",  # OpenAI, Anthropic (sk-ant-…)
        r"\b(?P<p3>gh[pousr]_|github_pat_)[A-Za-z0-9_]{22,}",  # GitHub
        r"\b(?P<p4>xox[abprs]-)[A-Za-z0-9\-]{10,}",  # Slack
        r"\b(?P<p5>AIza)[0-9A-Za-z_\-]{35}",  # Google API key
        r"\b(?P<p6>[rs]k_(?:live|test)_)[A-Za-z0-9]{16,}",  # Stripe
        r"\b(?P<p7>eyJ)[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",  # JWT
    ])
)


def _token(m: re.Match) -> str:
    prefix = next(v for k, v in m.groupdict().items() if v is not None)
    return prefix + MARK


# Valori che sono per intero un segnaposto: si lasciano in chiaro. Basta una parte non da segnaposto
# (Changeme2024!, a8Fxxxx93k, your_secret_9f8e) perché il valore venga oscurato.
_PLACEHOLDER = re.compile(
    r"(?i)^(?:<[^<>]*>|\$\{[^}]*\}|\{\{[^}]*\}\}|your[-_ ][a-z_\- ]*|changeme|change[-_]me|placeholder"
    r"|replace[-_]?me|x{4,}|\*{3,})$"
)


def _literal(m: re.Match) -> str:
    """Oscura il valore (gruppo v) salvo i segnaposto."""
    if _PLACEHOLDER.match(m.group("v")):
        return m.group(0)
    return m.group(0)[:m.start("v") - m.start()] + MARK + m.group(0)[m.end("v") - m.start():]


# Blocco di chiave privata, anche su più righe del diff (fino all'END o alla fine del testo, se troncato).
_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)", re.DOTALL
)

# Password in una connection string: scheme://utente:password@host
_URL_PASSWORD = re.compile(r"(\b[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)[^\s@/]+(@)")

_SECRET_NAME = r"[\w.\-]*(?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|(?:signing|encryption|hmac|master)[_\-]?key|credential)[\w.\-]*"

# Assegnazione di un letterale tra virgolette a un nome che sembra un segreto: password = "…", "token": "…"
_QUOTED = re.compile(
    rf"""((?i:{_SECRET_NAME})["']?\s*(?::=|=|:)\s*)(["'])(?![^"'\n]*\[REDACTED\])(?P<v>[^"'\n]{{6,}})\2"""
)

# Stesso caso senza virgolette (.env, YAML, ini): API_KEY=abc123…, password: abc123…
_BARE = re.compile(
    rf"""(^[+\- ]?\s*(?:export\s+)?(?i:{_SECRET_NAME})\s*[:=]\s*)(?!\S*\[REDACTED\])(?P<v>[A-Za-z0-9+/=_\-]{{8,}})\s*$""",
    re.MULTILINE,
)


def redact(text: str) -> Tuple[str, int]:
    """Restituisce il testo con i segreti sostituiti da [REDACTED] e quante sostituzioni ha fatto."""
    total = 0

    def counted(repl):
        def sub(m: re.Match) -> str:
            nonlocal total
            out = repl(m) if callable(repl) else m.expand(repl)
            total += out != m.group(0)  # i segnaposto lasciati in chiaro non contano
            return out
        return sub

    for pattern, repl in (
        (_PRIVATE_KEY, MARK),
        (_TOKENS, _token),
        (_URL_PASSWORD, rf"\1{MARK}\2"),
        (_QUOTED, _literal),
        (_BARE, _literal),
    ):
        text = pattern.sub(counted(repl), text)
    return text, total
