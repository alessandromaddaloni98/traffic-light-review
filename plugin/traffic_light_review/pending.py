"""Review proposta, in attesa della conferma dell'utente: diff completo e metadati per il subagent."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from traffic_light_review import rules
from traffic_light_review.decision import Decision, fmt_value
from traffic_light_review.diff import FileChange, file_patch
from traffic_light_review.jev import JevAnswers
from traffic_light_review.questions import CHECKS, CHOICE

PENDING_DIR = "pending"
DIFF_FILE = "review.diff"
META_FILE = "review.json"
RULES_DIR = "rules"


def build_patch(counted: List[FileChange], file_hunks: Optional[Dict[str, str]]) -> str:
    """Diff unificato dei soli file contati, con path relativi al progetto (senza old/ e new/)."""
    file_hunks = file_hunks or {}
    return "".join(file_patch(f, file_hunks.get(f.path, "")) for f in counted)


def focus(decision: Decision, relevant: Dict[str, List[str]]) -> List[dict]:
    """Domande da verificare per prime: regole scattate e check critici incerti, con i file rilevanti
    (relevant: check critico → file che corrispondono ai suoi escalation_patterns)."""
    items = [(h.check, h.value, f"regola {h.rule}" + (" (aggravante)" if h.aggravating else ""))
             for h in decision.hits]
    items += [(name, p, "Jev incerto") for name, p in decision.uncertain.items()]
    result, seen = [], set()
    for name, value, why in items:
        if name in seen:
            continue
        seen.add(name)
        check = CHECKS[name]
        entry = {"check": name, "question": check.instructions, "answer": fmt_value(name, value), "why": why,
                 "ocr_category": check.ocr_category}
        if relevant.get(name):
            entry["files"] = relevant[name]
        result.append(entry)
    return result


def clear(state: Path) -> None:
    shutil.rmtree(state / PENDING_DIR, ignore_errors=True)


def write(
    root: Path, state: Path, decision: Decision, answers: JevAnswers, counted: List[FileChange], file_hunks,
    relevant: Dict[str, List[str]],
) -> None:
    """Salva diff, regole OCR e metadati della review proposta."""
    folder = state / PENDING_DIR
    (folder / RULES_DIR).mkdir(parents=True, exist_ok=True)
    (folder / DIFF_FILE).write_text(build_patch(counted, file_hunks), encoding="utf-8")
    # Una regola per gruppo di file, già risolta come farebbe ocr delegate rule. I file cancellati non si rivedono.
    groups, warnings = rules.resolve(root, [f.path for f in counted if f.status != "D"])
    rule_groups = []
    for n, group in enumerate(groups, 1):
        name = f"{RULES_DIR}/{n}.md"
        (folder / name).write_text(group.text + "\n", encoding="utf-8")
        rule_groups.append({"rule_file": name, "source": group.source, "pattern": group.pattern, "files": group.files})
    meta = {
        "focus": focus(decision, relevant),
        "primary_concern": {
            "choice": answers.concern,
            "ocr_category": (CHECKS[CHOICE].ocr_categories or {}).get(answers.concern, "other"),
        },
        "scores": {name: fmt_value(name, v) for name, v in answers.scores.items()},
        "diff_available": file_hunks is not None,
        "rule_groups": rule_groups,
        "rule_warnings": warnings,
        "files": [
            {"path": f.path, "status": f.status, "added": f.added, "deleted": f.deleted}
            for f in counted
        ],
    }
    (folder / META_FILE).write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
