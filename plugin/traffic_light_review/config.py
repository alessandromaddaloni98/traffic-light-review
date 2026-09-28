"""Caricamento e validazione della configurazione (default del pacchetto + config personale sotto .git)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import FrozenSet, Optional, Tuple

import yaml

from traffic_light_review.questions import CHECKS, POLICY_CHECKS

DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.default.yaml")

SUMMARY_MODES = ("always", "unless_no_review", "above_threshold", "never")
CHECKS_MODES = ("triggered", "all")
RULE_OPS = ("gte", "lte")

# Tipo speciale per le liste di regole della policy (decision.review, decision.aggravating).
RULES = "rules"

# Tipo atteso per ogni chiave. Le chiavi non elencate qui sono un errore.
SCHEMA = {
    "files": {
        "include_extensions": list,
        "exclude_dirs": list,
        "exclude_paths": list,
        "max_file_kb": int,
        "max_scanned_files": int,
    },
    "prefilter": {
        "min_lines": int,
        "max_lines": int,
        "ignore_paths": list,
    },
    "report": {
        "summary": str,
        "max_listed_files": int,
        "checks": str,
    },
    "jev": {
        "model": str,
        "timeout_s": float,
    },
    "decision": {
        "review": RULES,
        "aggravating": RULES,
        "uncertainty_low": float,
        "uncertainty_high": float,
    },
}


class ConfigError(Exception):
    """Configurazione non valida. Il messaggio è pensato per l'utente."""


@dataclass(frozen=True)
class FilesConfig:
    include_extensions: FrozenSet[str]
    exclude_dirs: FrozenSet[str]
    exclude_paths: Tuple[str, ...]
    max_file_bytes: int
    max_scanned_files: int


@dataclass(frozen=True)
class PrefilterConfig:
    min_lines: int
    max_lines: int
    ignore_paths: Tuple[str, ...]


@dataclass(frozen=True)
class ReportConfig:
    summary: str
    max_listed_files: int
    checks: str


@dataclass(frozen=True)
class JevConfig:
    model: str
    timeout_s: float


@dataclass(frozen=True)
class Condition:
    check: str
    op: str  # gte | lte
    value: float

    def holds(self, values) -> bool:
        v = values[self.check]
        return v >= self.value if self.op == "gte" else v <= self.value

    def __str__(self) -> str:
        return f"{self.check} {'≥' if self.op == 'gte' else '≤'} {self.value:g}"


@dataclass(frozen=True)
class Rule:
    """Scatta se la condizione vale e nessuna delle condizioni in unless vale."""
    when: Condition
    unless: Tuple[Condition, ...] = ()

    def fires(self, values) -> bool:
        return self.when.holds(values) and not any(c.holds(values) for c in self.unless)


@dataclass(frozen=True)
class DecisionConfig:
    review: Tuple[Rule, ...]
    aggravating: Tuple[Rule, ...]
    uncertainty_low: float
    uncertainty_high: float


@dataclass(frozen=True)
class Config:
    files: FilesConfig
    prefilter: PrefilterConfig
    report: ReportConfig
    jev: JevConfig
    decision: DecisionConfig


def read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f" (riga {mark.line + 1})" if mark else ""
        raise ConfigError(f"{path.name}: YAML non valido{where}") from None
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path.name}: la radice deve essere una mappa di sezioni")
    return data


def _check_type(key: str, value, expected) -> None:
    if expected is RULES:
        if not isinstance(value, list) or not all(isinstance(v, dict) for v in value):
            raise ConfigError(f"{key} deve essere una lista di regole {{check, op, value[, unless]}}")
        return
    # bool è sottoclasse di int: va escluso esplicitamente. Dove serve un float va bene anche un intero.
    accepted = (int, float) if expected is float else expected
    ok = isinstance(value, accepted) and not (expected in (int, float) and isinstance(value, bool))
    if not ok:
        raise ConfigError(f"{key} deve essere di tipo {expected.__name__}")
    if expected is list and not all(isinstance(v, str) for v in value):
        raise ConfigError(f"{key} deve contenere solo stringhe")


def _merge(base: dict, override: dict, source: str) -> dict:
    merged = {section: dict(values) for section, values in base.items()}
    for section, values in override.items():
        if section not in SCHEMA:
            raise ConfigError(f"{source}: sezione sconosciuta '{section}'")
        if not isinstance(values, dict):
            raise ConfigError(f"{source}: la sezione '{section}' deve essere una mappa")
        for key, value in values.items():
            if key not in SCHEMA[section]:
                raise ConfigError(f"{source}: chiave sconosciuta '{section}.{key}'")
            merged.setdefault(section, {})[key] = value
    return merged


def _condition(key: str, raw: dict) -> Condition:
    if not isinstance(raw, dict) or set(raw) != {"check", "op", "value"}:
        raise ConfigError(f"{key}: ogni condizione deve avere esattamente check, op e value")
    name, op, value = raw["check"], raw["op"], raw["value"]
    if name not in POLICY_CHECKS:
        why = " (è il contro-esempio, fuori dalla policy per scelta)" if name in CHECKS else ""
        raise ConfigError(f"{key}: check '{name}' non usabile{why}; validi: {', '.join(POLICY_CHECKS)}")
    if op not in RULE_OPS:
        raise ConfigError(f"{key}: op deve essere uno tra: {', '.join(RULE_OPS)}")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{key}: value deve essere un numero")
    check = CHECKS[name]
    top = check.levels - 1 if check.type == "score" else 1
    if not 0 <= value <= top:
        raise ConfigError(f"{key}: value di {name} deve stare tra 0 e {top}")
    return Condition(check=name, op=op, value=float(value))


def _rules(key: str, raw: list) -> Tuple[Rule, ...]:
    rules = []
    for n, item in enumerate(raw):
        where = f"{key}[{n}]"
        unknown = set(item) - {"check", "op", "value", "unless"}
        if unknown:
            raise ConfigError(f"{where}: campi sconosciuti {sorted(unknown)}")
        unless = item.get("unless", [])
        unless = [unless] if isinstance(unless, dict) else unless
        if not isinstance(unless, list):
            raise ConfigError(f"{where}.unless deve essere una condizione o una lista di condizioni")
        rules.append(Rule(
            when=_condition(where, {k: v for k, v in item.items() if k != "unless"}),
            unless=tuple(_condition(f"{where}.unless", c) for c in unless),
        ))
    return tuple(rules)


def _normalize_ext(ext: str) -> str:
    ext = ext.strip().lower()
    return ext if ext.startswith(".") else "." + ext


def load_config(personal_file: Optional[Path]) -> Config:
    """Default del pacchetto più la config personale, se il file esiste."""
    personal = read_yaml(personal_file) if personal_file is not None and personal_file.is_file() else {}
    return build_config(personal, personal_file.name if personal_file is not None else "config personale")


def build_config(personal: dict, source: str = "config personale") -> Config:
    """Valida la config risultante da default e chiavi personali (le liste sostituiscono quelle di default)."""
    raw = _merge({}, read_yaml(DEFAULT_CONFIG_PATH), DEFAULT_CONFIG_PATH.name)
    raw = _merge(raw, personal, source)

    for section, keys in SCHEMA.items():
        for key, expected in keys.items():
            if key not in raw.get(section, {}):
                raise ConfigError(f"manca la chiave '{section}.{key}'")
            _check_type(f"{section}.{key}", raw[section][key], expected)

    s, p, r, j, d = raw["files"], raw["prefilter"], raw["report"], raw["jev"], raw["decision"]
    if s["max_file_kb"] <= 0 or s["max_scanned_files"] <= 0:
        raise ConfigError("files.max_file_kb e files.max_scanned_files devono essere > 0")
    if p["min_lines"] < 0:
        raise ConfigError("prefilter.min_lines deve essere >= 0")
    if p["max_lines"] <= p["min_lines"]:
        raise ConfigError("prefilter.max_lines deve essere > prefilter.min_lines")
    if r["summary"] not in SUMMARY_MODES:
        raise ConfigError(f"report.summary deve essere uno tra: {', '.join(SUMMARY_MODES)}")
    if r["max_listed_files"] < 0:
        raise ConfigError("report.max_listed_files deve essere >= 0")
    if r["checks"] not in CHECKS_MODES:
        raise ConfigError(f"report.checks deve essere uno tra: {', '.join(CHECKS_MODES)}")
    if not j["model"]:
        raise ConfigError("jev.model non può essere vuoto")
    if j["timeout_s"] <= 0:
        raise ConfigError("jev.timeout_s deve essere > 0")
    if not 0 <= d["uncertainty_low"] <= d["uncertainty_high"] <= 1:
        raise ConfigError("serve 0 <= decision.uncertainty_low <= decision.uncertainty_high <= 1")

    return Config(
        files=FilesConfig(
            include_extensions=frozenset(_normalize_ext(e) for e in s["include_extensions"]),
            exclude_dirs=frozenset(s["exclude_dirs"]),
            exclude_paths=tuple(s["exclude_paths"]),
            max_file_bytes=s["max_file_kb"] * 1024,
            max_scanned_files=s["max_scanned_files"],
        ),
        prefilter=PrefilterConfig(
            min_lines=p["min_lines"],
            max_lines=p["max_lines"],
            ignore_paths=tuple(p["ignore_paths"]),
        ),
        report=ReportConfig(
            summary=r["summary"],
            max_listed_files=r["max_listed_files"],
            checks=r["checks"],
        ),
        jev=JevConfig(
            model=j["model"],
            timeout_s=float(j["timeout_s"]),
        ),
        decision=DecisionConfig(
            review=_rules("decision.review", d["review"]),
            aggravating=_rules("decision.aggravating", d["aggravating"]),
            uncertainty_low=float(d["uncertainty_low"]),
            uncertainty_high=float(d["uncertainty_high"]),
        ),
    )
