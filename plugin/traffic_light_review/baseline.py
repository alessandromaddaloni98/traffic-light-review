"""Baseline del diff con un indice git separato e git write-tree.

A ogni Stop l'indice separato (GIT_INDEX_FILE) viene allineato ai file di codice attuali e git write-tree ne dà
l'hash del tree: il diff è quello tra la baseline (il tree dell'ultima review proposta, o dell'ultima ripartenza) e
quello di adesso. last_tree tiene il tree dell'ultimo Stop, per saltare i turni senza modifiche. Nessun commit,
nessuna ref, e l'indice vero dell'utente non viene mai scritto. Tutto lo stato sta sotto .git.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

NOT_A_REPO_MESSAGE = "traffic-light-review: qui non c'è git, non controllo niente. Fai git init per attivarmi."

STATE_NAME = "traffic_light_review"
CONFIG_FILE = "config.yaml"
# Indice separato, nella cartella di stato.
INDEX_FILE = "index"
# Hash del tree dell'ultima review proposta (o dell'ultima ripartenza): base del diff.
BASELINE_FILE = "baseline"
# Hash del tree dell'ultimo Stop: se non cambia, niente diff né Jev.
LAST_TREE_FILE = "last_tree"
# Progetti in sottocartelle della stessa repo: ognuno ha la sua cartella di stato.
SUB_DIR = "sub"


class GitError(Exception):
    """Un comando git è fallito. Il messaggio contiene solo il comando e l'exit code."""


@dataclass(frozen=True)
class Repo:
    root: Path  # cartella del progetto, cwd di tutti i comandi git
    config_file: Path  # config personale, una per repo (condivisa dai worktree)
    state: Path  # stato del progetto, per worktree e per sottocartella


def _decode(out: bytes) -> str:
    return out.decode("utf-8", errors="surrogateescape")


def find_repo(root: Path) -> Optional[Repo]:
    """Individua la repo che contiene root. None se git manca o root non è in una working tree."""
    try:
        proc = subprocess.run(
            # --show-toplevel fallisce anche dentro .git o in una repo bare: niente working tree, niente triage.
            ["git", "rev-parse", "--show-toplevel", "--show-prefix",
             "--path-format=absolute", "--git-common-dir",
             "--path-format=absolute", "--git-path", STATE_NAME],
            cwd=root,
            capture_output=True,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    lines = _decode(proc.stdout).split("\n")
    if len(lines) < 4:
        return None
    _, prefix, common_dir, git_path = lines[:4]
    state = Path(git_path)
    if prefix:
        state = state / SUB_DIR / hashlib.sha256(prefix.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
    return Repo(root=root, config_file=Path(common_dir) / STATE_NAME / CONFIG_FILE, state=state)


def _git(repo: Repo, args: List[str], stdin: bytes = b"", index: bool = True) -> bytes:
    env = None
    if index:
        # Solo per i comandi sull'indice separato. Pathspec letterali: un file "w*.py" non è un glob.
        env = {**os.environ, "GIT_INDEX_FILE": str(repo.state / INDEX_FILE), "GIT_LITERAL_PATHSPECS": "1"}
    proc = subprocess.run(["git", *args], cwd=repo.root, env=env, input=stdin, capture_output=True)
    if proc.returncode != 0:
        raise GitError(f"git {args[0]} fallito (exit {proc.returncode})")
    return proc.stdout


def _nul_list(paths: List[str]) -> bytes:
    return "".join(p + "\0" for p in paths).encode("utf-8", errors="surrogateescape")


def _write_tree(repo: Repo, files: List[Path]) -> str:
    # Voci dell'indice separato sotto root (una sola volta anche se in conflitto), relative a root.
    listed = dict.fromkeys(p for p in _decode(_git(repo, ["ls-files", "-z"])).split("\0") if p)
    current = [rel.as_posix() for rel in files]
    wanted = set(current)
    stale = [p for p in listed if p not in wanted]
    if stale:
        # Cancellati o usciti dai filtri. git rm --cached --pathspec-from-file qui fallisce.
        _git(repo, ["update-index", "--force-remove", "-z", "--stdin"], _nul_list(stale))
    if current:
        _git(repo, ["add", "-f", "--pathspec-from-file=-", "--pathspec-file-nul"], _nul_list(current))
    return _decode(_git(repo, ["write-tree"])).strip()


def _real_index(repo: Repo) -> Optional[Path]:
    try:
        out = _git(repo, ["rev-parse", "--path-format=absolute", "--git-path", "index"], index=False)
    except GitError:
        return None
    path = Path(_decode(out).strip())
    return path if path.is_file() else None


def update_tree(repo: Repo, files: List[Path]) -> str:
    """Allinea l'indice separato ai file di codice attuali e restituisce l'hash del tree."""
    index = repo.state / INDEX_FILE
    if not index.exists():
        # Seed dall'indice vero: le sue stat evitano di rileggere tutti i file tracciati. Il tree è lo stesso.
        seed = _real_index(repo)
        if seed is not None:
            try:
                shutil.copyfile(seed, index)
                return _write_tree(repo, files)
            except (OSError, GitError):
                index.unlink(missing_ok=True)
    return _write_tree(repo, files)


def tree_exists(repo: Repo, tree: str) -> bool:
    """False se l'oggetto non c'è più, per esempio dopo git gc --prune=now."""
    try:
        _git(repo, ["cat-file", "-e", f"{tree}^{{tree}}"], index=False)
    except GitError:
        return False
    return True


def _read_tree(repo: Repo, name: str) -> Optional[str]:
    try:
        tree = (repo.state / name).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tree or None


def read_baseline(repo: Repo) -> Optional[str]:
    return _read_tree(repo, BASELINE_FILE)


def write_baseline(repo: Repo, tree: str) -> None:
    (repo.state / BASELINE_FILE).write_text(tree + "\n", encoding="utf-8")


def read_last_tree(repo: Repo) -> Optional[str]:
    return _read_tree(repo, LAST_TREE_FILE)


def write_last_tree(repo: Repo, tree: str) -> None:
    (repo.state / LAST_TREE_FILE).write_text(tree + "\n", encoding="utf-8")


def clear(repo: Repo) -> None:
    """Cancella baseline, last_tree e indice separato: il turno dopo riparte come al primo avvio."""
    for name in (BASELINE_FILE, LAST_TREE_FILE, INDEX_FILE):
        (repo.state / name).unlink(missing_ok=True)
