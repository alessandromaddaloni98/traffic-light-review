"""Diff tra due tree git (baseline dell'ultima review e tree di adesso), con path relativi al progetto."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class FileChange:
    path: str
    status: str  # "A" aggiunto, "M" modificato, "D" cancellato
    added: Optional[int]  # None se binario
    deleted: Optional[int]


@dataclass
class DiffSummary:
    files: List[FileChange]

    @property
    def added(self) -> int:
        return sum(f.added or 0 for f in self.files)

    @property
    def deleted(self) -> int:
        return sum(f.deleted or 0 for f in self.files)


def _git_diff(root: Path, tree_prev: str, tree_now: str, *args: str) -> bytes:
    proc = subprocess.run(
        # --no-color e --no-ext-diff: il .gitconfig dell'utente non deve cambiare il formato dell'output.
        # --relative: solo i file sotto root, con path relativi a root.
        ["git", "diff", "--no-renames", "--no-color", "--no-ext-diff", "--relative", *args, tree_prev, tree_now],
        cwd=root,
        capture_output=True,
    )
    # Tra due tree git diff esce con 0 anche se ci sono differenze: ogni altro exit code è un errore.
    if proc.returncode != 0:
        raise RuntimeError(f"git diff fallito (exit {proc.returncode})")
    return proc.stdout


def hunks(root: Path, tree_prev: str, tree_now: str, summary: DiffSummary) -> Optional[Dict[str, str]]:
    """Hunk di ogni file (dal primo "@@" in poi), per path. Le sezioni sono nello stesso ordine di numstat.

    Restituisce None se il numero di sezioni non torna con numstat: meglio nessun hunk che hunk sbagliati.
    """
    text = _git_diff(root, tree_prev, tree_now, "--unified=3").decode("utf-8", errors="replace")
    sections = text.split("\ndiff --git ")
    if sections and not sections[0].startswith("diff --git "):
        sections = sections[1:]  # output vuoto
    if len(sections) != len(summary.files):
        return None
    result = {}
    for f, section in zip(summary.files, sections):
        start = section.find("\n@@")
        result[f.path] = section[start + 1:] if start != -1 else ""  # "" per binari o solo cambio permessi
    return result


def numstat(root: Path, tree_prev: str, tree_now: str) -> DiffSummary:
    """File cambiati tra i due tree, con stato e righe +/−. I path restituiti sono relativi a root."""
    # Con -z prima vengono i record --raw (":100644 100644 <old> <new> M\0path\0"), poi quelli --numstat
    # ("added\tdeleted\tpath\0"). Tra due tree numstat non dice se il file è aggiunto o cancellato: lo dice raw.
    fields = _git_diff(root, tree_prev, tree_now, "--raw", "--numstat", "-z").decode(
        "utf-8", errors="replace").split("\0")
    statuses: Dict[str, str] = {}
    files: List[FileChange] = []
    i = 0
    while i < len(fields):
        field = fields[i]
        if field.startswith(":"):
            status = field.rsplit(" ", 1)[1][:1]
            statuses[fields[i + 1]] = status if status in ("A", "D") else "M"
            i += 2
            continue
        if field:
            added, deleted, path = field.split("\t", 2)
            files.append(FileChange(
                path=path,
                status=statuses.get(path, "M"),
                added=None if added == "-" else int(added),
                deleted=None if deleted == "-" else int(deleted),
            ))
        i += 1
    return DiffSummary(files=files)


def file_patch(f: FileChange, body: str) -> str:
    """Sezione di diff unificato di un file, con path relativi al progetto (a/ e b/)."""
    old = "/dev/null" if f.status == "A" else f"a/{f.path}"
    new = "/dev/null" if f.status == "D" else f"b/{f.path}"
    mode = {"A": "new file mode 100644\n", "D": "deleted file mode 100644\n"}.get(f.status, "")
    body = "Binary files differ" if f.added is None else body.rstrip("\n")
    return f"diff --git a/{f.path} b/{f.path}\n{mode}--- {old}\n+++ {new}\n{body}\n"
