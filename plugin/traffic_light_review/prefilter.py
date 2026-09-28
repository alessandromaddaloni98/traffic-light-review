"""Pre-filtro deterministico: decide se il diff merita di essere giudicato da Jev."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from traffic_light_review.config import PrefilterConfig
from traffic_light_review.diff import DiffSummary, FileChange
from traffic_light_review.globs import matches_any


@dataclass
class PrefilterResult:
    passed: bool
    counted: List[FileChange]  # file che contano per la soglia
    ignored: List[FileChange]  # file presenti nel diff ma esclusi dal conteggio
    reason: str
    lines: int  # righe cambiate (+ e −) sui file contati


def run_prefilter(summary: DiffSummary, cfg: PrefilterConfig) -> PrefilterResult:
    counted, ignored = [], []
    for f in summary.files:
        (ignored if matches_any(f.path, cfg.ignore_paths) else counted).append(f)

    # I binari non hanno righe: non spostano la soglia.
    lines = sum((f.added or 0) + (f.deleted or 0) for f in counted)

    if not counted:
        passed, reason = False, "solo file ignorati"
    elif lines < cfg.min_lines:
        passed, reason = False, f"{lines} righe < soglia {cfg.min_lines}"
    else:
        passed, reason = True, f"{lines} righe ≥ soglia {cfg.min_lines}"
    return PrefilterResult(passed=passed, counted=counted, ignored=ignored, reason=reason, lines=lines)
