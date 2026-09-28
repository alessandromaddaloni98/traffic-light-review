"""Scelta delle regole di review per file, replicando il resolver di Open Code Review senza la sua CLI.

Le regole di sistema sono copiate da OCR in ocr_rules/ (Apache-2.0, vedi ocr_rules/README.md).
Come in OCR (internal/config/rules):
  - livelli in ordine di priorità: .opencodereview/rule.json del progetto, ~/.opencodereview/rule.json, sistema;
  - in ogni livello vince il primo pattern che corrisponde, senza distinzione tra maiuscole e minuscole;
  - una regola utente con merge_system_rule si aggiunge a quella di sistema invece di sostituirla;
  - un file .m la cui prima riga sembra Objective-C usa objc.md invece di matlab.md.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

RULES_DIR = Path(__file__).with_name("ocr_rules")
PROJECT_RULE_FILE = Path(".opencodereview") / "rule.json"
RULE_FILE_EXTS = {".md", ".txt", ".markdown"}
MAX_RULE_FILE_BYTES = 512 * 1024
OBJC_PREFIXES = (
    "#import", "#include", "#pragma", "#if", "#define",
    "@import", "@interface", "@implementation", "@class", "@protocol",
    "//", "/*",
)


@dataclass
class RuleGroup:
    text: str
    source: str  # project | global | system
    pattern: str  # glob che ha vinto, o "default"
    files: List[str]


def _expand_braces(pattern: str) -> List[str]:
    """Come expandBraces di OCR: espande solo il primo gruppo {a,b,c}."""
    start = pattern.find("{")
    end = pattern.find("}", start + 1) if start >= 0 else -1
    if start < 0 or end < 0:
        return [pattern]
    return [pattern[:start] + opt + pattern[end + 1:] for opt in pattern[start + 1:end].split(",")]


def _segment_regex(segment: str) -> str:
    out, i = [], 0
    while i < len(segment):
        c = segment[i]
        if c == "*":
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "[" and "]" in segment[i + 1:]:
            end = segment.index("]", i + 1)
            body = segment[i + 1:end]
            out.append("[" + ("^" + body[1:] if body.startswith("!") else body) + "]")
            i = end
        else:
            out.append(re.escape(c))
        i += 1
    return "".join(out)


@lru_cache(maxsize=None)
def _compile(pattern: str) -> Tuple[re.Pattern, ...]:
    """Glob in stile doublestar: "**" come segmento intero vale zero o più cartelle, "*" non attraversa "/"."""
    compiled = []
    for expanded in _expand_braces(pattern.lower()):
        parts = expanded.split("/")
        regex = ""
        for idx, part in enumerate(parts):
            last = idx == len(parts) - 1
            if part == "**":
                regex += ".*" if last else "(?:[^/]*/)*"
            else:
                regex += _segment_regex(part) + ("" if last else "/")
        compiled.append(re.compile(regex))
    return tuple(compiled)


def glob_match(pattern: str, path: str) -> bool:
    return any(rx.fullmatch(path.lower()) for rx in _compile(pattern))


@lru_cache(maxsize=1)
def _system() -> Tuple[str, Tuple[Tuple[str, str], ...], str]:
    """(regola di default, [(pattern, regola)] in ordine, regola objc)."""
    raw = json.loads((RULES_DIR / "system_rules.json").read_text(encoding="utf-8"))
    doc = lambda name: (RULES_DIR / "rule_docs" / name).read_text(encoding="utf-8").rstrip("\n")
    # json.loads conserva l'ordine delle chiavi, che conta: vince il primo pattern.
    ordered = tuple((pattern, doc(name)) for pattern, name in raw["path_rule_map"].items())
    return doc(raw["default_rule"]), ordered, doc("objc.md")


def _looks_like_objc(path: Path) -> bool:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if line.strip():
                    return line.strip().startswith(OBJC_PREFIXES)
    except OSError:
        pass
    return False


def _system_rule(root: Path, rel: str) -> Tuple[str, str]:
    default, ordered, objc = _system()
    for pattern, text in ordered:
        if glob_match(pattern, rel):
            if rel.lower().endswith(".m") and _looks_like_objc(root / rel):
                return objc, pattern
            return text, pattern
    return default, "default"


def _within(base: Path, path: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def _rule_text(value: str, base: Path, confine: Optional[Path]) -> str:
    """Una regola utente è testo, oppure il path di un file .md/.txt/.markdown (una riga senza spazi)."""
    if "\n" in value or " " in value or Path(value).suffix.lower() not in RULE_FILE_EXTS:
        return value
    path = Path(value) if Path(value).is_absolute() else base / value
    try:
        resolved = path.resolve(strict=True)
        if confine is not None and not _within(confine, resolved):
            return ""
        if resolved.suffix.lower() not in RULE_FILE_EXTS or resolved.stat().st_size > MAX_RULE_FILE_BYTES:
            return ""
        return resolved.read_text(encoding="utf-8").rstrip("\n")
    except OSError:
        return ""


def _load_layer(path: Path, base: Path, confine: Optional[Path], warnings: List[str], label: str) -> List[dict]:
    if not path.is_file():
        return []
    if confine is not None and not _within(confine, path.resolve()):
        warnings.append(f"{label}: il file punta fuori dal progetto, ignorato")
        return []
    try:
        entries = json.loads(path.read_text(encoding="utf-8")).get("rules") or []
    except (OSError, ValueError, AttributeError):
        warnings.append(f"{label}: JSON non valido, ignorato")
        return []
    layer = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            continue
        rule = entry.get("rule") if isinstance(entry.get("rule"), str) else ""
        if rule.strip():
            rule = _rule_text(rule, base, confine)
        merge = bool(entry.get("merge_system_rule"))
        if rule or merge:
            layer.append({"path": entry["path"], "rule": rule, "merge": merge})
    return layer


def resolve(root: Path, paths: List[str], home: Optional[Path] = None) -> Tuple[List[RuleGroup], List[str]]:
    """Regola di ogni file, raggruppando come OCR i file con stessa regola, sorgente e pattern. Restituisce anche gli avvisi sui rule.json."""
    warnings: List[str] = []
    root = root.resolve()
    home = home if home is not None else Path.home()
    layers = [
        ("project", _load_layer(root / PROJECT_RULE_FILE, root, root, warnings, str(PROJECT_RULE_FILE))),
        ("global", _load_layer(home / PROJECT_RULE_FILE, home / ".opencodereview", None, warnings,
                               "~/" + PROJECT_RULE_FILE.as_posix())),
    ]
    groups: Dict[Tuple[str, str, str], RuleGroup] = {}
    for rel in paths:
        text, source, pattern = None, "system", "default"
        for name, layer in layers:
            entry = next((e for e in layer if glob_match(e["path"], rel)), None)
            if entry is None:
                continue
            source, pattern, text = name, entry["path"], entry["rule"]
            if entry["merge"]:
                system_text = _system_rule(root, rel)[0]
                text = (
                    "## System-Specific Rules (Mandatory)\n\n" + system_text
                    + "\n\n---\n\n## User-Specific Rules (Mandatory)\n\n" + text
                ) if text else system_text
            break
        if text is None:
            text, pattern = _system_rule(root, rel)
        group = groups.setdefault((text, source, pattern), RuleGroup(text=text, source=source, pattern=pattern, files=[]))
        group.files.append(rel)
    return list(groups.values()), warnings
