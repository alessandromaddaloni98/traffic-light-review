"""Raccolta dei file di codice del progetto, da git ls-files (quindi sempre nel rispetto dei .gitignore)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterator, List

from traffic_light_review.config import FilesConfig
from traffic_light_review.globs import matches_any


class TooManyFiles(Exception):
    """Il progetto ha più file da esaminare di files.max_scanned_files."""


def _is_secret_file(name: str) -> bool:
    # Regola di progetto: i file .env non vanno mai aperti né copiati.
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def _git_candidates(root: Path) -> List[Path]:
    """File tracciati o non tracciati ma non ignorati, relativi a root. La repo è già stata verificata dal chiamante."""
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git ls-files fallito (exit {proc.returncode})")
    # Un file in conflitto compare più volte: si tiene una sola copia, in ordine.
    names = dict.fromkeys(p for p in proc.stdout.decode("utf-8", errors="surrogateescape").split("\0") if p)
    return [Path(p) for p in names]


def iter_code_files(root: Path, cfg: FilesConfig) -> Iterator[Path]:
    """Restituisce i path relativi dei file di codice sotto root, già filtrati."""
    for scanned, rel in enumerate(_git_candidates(root), 1):
        # Il limite conta tutti i candidati di git ls-files, non solo quelli di codice.
        if scanned > cfg.max_scanned_files:
            raise TooManyFiles()
        if _is_secret_file(rel.name) or rel.suffix.lower() not in cfg.include_extensions:
            continue
        # git ls-files non pota le cartelle: i filtri sulle cartelle si applicano qui.
        if any(part in cfg.exclude_dirs for part in rel.parts[:-1]):
            continue
        path = root / rel
        if path.is_symlink() or matches_any(rel.as_posix(), cfg.exclude_paths):
            continue
        try:
            if not path.is_file() or path.stat().st_size > cfg.max_file_bytes:
                continue
        except OSError:
            continue
        yield rel
