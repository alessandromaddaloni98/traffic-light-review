"""Confronto di path relativi con pattern in stile gitignore semplificato."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import Iterable


def matches_any(rel_path: str, patterns: Iterable[str]) -> bool:
    """True se rel_path (posix, relativo alla root) o una sua cartella padre corrisponde a un pattern.

    Pattern senza "/" confrontano il solo nome; pattern con "/" il path intero.
    Un "**/" iniziale corrisponde anche alla root.
    """
    parts = rel_path.split("/")
    candidates = ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]
    for pat in patterns:
        anchored = "/" in pat
        alt = pat[3:] if pat.startswith("**/") else pat
        for cand in candidates:
            target = cand if anchored else cand.rsplit("/", 1)[-1]
            if fnmatchcase(target, pat) or fnmatchcase(target, alt):
                return True
    return False
