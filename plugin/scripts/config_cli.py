# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6",
# ]
# ///
"""Config personale di traffic-light-review, usata dalla skill /traffic-light-review:config.

Uso: uv run --script plugin/scripts/config_cli.py [show] | set <sezione.chiave> <valore> | reset [<sezione.chiave>]

Le chiavi personali stanno in .git/traffic_light_review/config.yaml della repo che contiene la cartella corrente.
"""

import sys
from pathlib import Path
from typing import List, Optional, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from traffic_light_review.baseline import NOT_A_REPO_MESSAGE, Repo, find_repo  # noqa: E402
from traffic_light_review.config import (  # noqa: E402
    DEFAULT_CONFIG_PATH, SCHEMA, ConfigError, build_config, read_yaml,
)

USAGE = "uso: /traffic-light-review:config [set <sezione.chiave> <valore> | reset [<sezione.chiave>]]"


class CliError(Exception):
    """Errore da mostrare all'utente così com'è."""


def _split_key(key: str) -> Tuple[str, str]:
    section, _, name = key.partition(".")
    if name not in SCHEMA.get(section, {}):
        valid = ", ".join(f"{s}.{k}" for s, keys in SCHEMA.items() for k in keys)
        raise CliError(f"chiave sconosciuta '{key}'. Chiavi valide: {valid}")
    return section, name


def _personal(repo: Repo) -> dict:
    """Chiavi personali già salvate. Un file rovinato a mano si sistema solo con reset."""
    try:
        personal = read_yaml(repo.config_file) if repo.config_file.is_file() else {}
        build_config(personal)
    except ConfigError as exc:
        raise CliError(f"config personale non valida: {exc}. Usa reset per tornare ai default") from None
    return personal


def _save(repo: Repo, personal: dict) -> None:
    """Valida e scrive la config personale. Se non resta nessuna chiave, cancella il file."""
    personal = {section: values for section, values in personal.items() if values}
    try:
        build_config(personal)
    except ConfigError as exc:
        raise CliError(f"non salvo, la config sarebbe non valida: {exc}") from None
    if not personal:
        repo.config_file.unlink(missing_ok=True)
        return
    repo.config_file.parent.mkdir(parents=True, exist_ok=True)
    repo.config_file.write_text(
        yaml.safe_dump(personal, sort_keys=False, allow_unicode=True, default_flow_style=None), encoding="utf-8"
    )


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return "[" + ", ".join(value) + "]"
    if isinstance(value, (list, dict)):
        # Regole della policy: YAML su una riga, lo stesso formato che accetta set.
        return yaml.safe_dump(value, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10**6).strip()
    return str(value)


def _line(section: str, name: str, value, source: str) -> str:
    return f"{section}.{name} = {_fmt(value)}  ({source})"


def show(repo: Repo) -> str:
    """Config effettiva, una riga per chiave, con la fonte di ogni valore."""
    defaults = read_yaml(DEFAULT_CONFIG_PATH)
    personal = _personal(repo)
    lines = []
    for section, keys in SCHEMA.items():
        for name in keys:
            mine = personal.get(section) or {}
            if name in mine:
                lines.append(_line(section, name, mine[name], "personale"))
            else:
                lines.append(_line(section, name, defaults[section][name], "default"))
    return "\n".join(lines)


def set_value(repo: Repo, key: str, raw_value: str) -> str:
    section, name = _split_key(key)
    try:
        value = yaml.safe_load(raw_value)
    except yaml.YAMLError:
        raise CliError(f"valore non valido per {key}: {raw_value!r} non è YAML valido") from None
    personal = _personal(repo)
    personal.setdefault(section, {})[name] = value
    _save(repo, personal)
    return _line(section, name, value, "personale")


def reset(repo: Repo, key: Optional[str] = None) -> str:
    if key is None:
        if not repo.config_file.is_file():
            return "nessuna config personale: valgono già i default"
        repo.config_file.unlink()
        return "config personale cancellata: tornano i default"
    section, name = _split_key(key)
    personal = _personal(repo)
    if name not in (personal.get(section) or {}):
        return f"{key} non è nella config personale: vale già il default"
    del personal[section][name]
    _save(repo, personal)
    default = read_yaml(DEFAULT_CONFIG_PATH)[section][name]
    return _line(section, name, default, "default")


def run(argv: List[str], cwd: Path) -> Tuple[int, str]:
    """Esegue il comando e restituisce exit code e testo da stampare."""
    repo = find_repo(cwd)
    if repo is None:
        return 1, NOT_A_REPO_MESSAGE
    command, args = (argv[0], argv[1:]) if argv else ("show", [])
    try:
        if command == "show" and not args:
            return 0, show(repo)
        if command == "set" and len(args) == 2:
            return 0, set_value(repo, args[0], args[1])
        if command == "reset" and len(args) <= 1:
            return 0, reset(repo, args[0] if args else None)
    except CliError as exc:
        return 1, str(exc)
    return 2, USAGE


def main() -> int:
    code, text = run(sys.argv[1:], Path.cwd())
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
