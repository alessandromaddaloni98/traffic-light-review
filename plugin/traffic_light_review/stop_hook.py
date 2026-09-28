"""Stop hook di Claude Code: a fine turno confronta i file di codice con la baseline dell'ultima review
(git write-tree su un indice separato), applica il pre-filtro e, se serve, chiede a Jev un giudizio sul diff.

Si lancia tramite scripts/stop_hook.py (uv run --script), che porta con sé le dipendenze.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from traffic_light_review import baseline, jev, pending
from traffic_light_review.config import Config, ConfigError, ReportConfig, load_config
from traffic_light_review.decision import Decision, all_checks, decide, describe, uncertainty_line, reasons
from traffic_light_review.diff import DiffSummary, hunks, numstat
from traffic_light_review.files import TooManyFiles, iter_code_files
from traffic_light_review.prefilter import PrefilterResult, run_prefilter

# Impronta dei filtri con cui è stata costruita la baseline.
FINGERPRINT_FILE = "filters.sha256"
# Ultime risposte di Jev e decisione presa, utili per tarare le soglie. Niente codice, niente chiavi.
LAST_TRIAGE_FILE = "last_triage.json"
# Storico di tutti i triage, una riga JSON ciascuno, per tarare le soglie. Tiene solo le ultime righe.
TRIAGE_LOG_FILE = "triage_log.jsonl"
TRIAGE_LOG_MAX_LINES = 500
# Presente se l'ultimo turno ha trovato troppi file: il messaggio compare solo la prima volta.
TOO_MANY_FILE = "too_many_files"
# Subagent della review, definito in agents/reviewer.md: Claude Code lo registra come <plugin>:<agent>.
REVIEW_AGENT = "traffic-light-review:reviewer"


def _fingerprint(config: Config) -> str:
    f = config.files
    payload = json.dumps(
        [sorted(f.include_extensions), sorted(f.exclude_dirs), list(f.exclude_paths), f.max_file_bytes]
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _read_fingerprint(state: Path) -> str:
    try:
        return (state / FINGERPRINT_FILE).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_fingerprint(state: Path, config: Config) -> None:
    (state / FINGERPRINT_FILE).write_text(_fingerprint(config) + "\n", encoding="utf-8")


def _too_many_files(repo: baseline.Repo, config: Config) -> dict:
    # Repo troppo grande: niente baseline, e l'avviso una volta sola.
    baseline.clear(repo)
    marker = repo.state / TOO_MANY_FILE
    if marker.exists():
        return {}
    marker.write_text("", encoding="utf-8")
    limit = config.files.max_scanned_files
    return {"systemMessage": (
        f"traffic-light-review: più di {limit} file, mi spengo qui. "
        f"Alza files.max_scanned_files o escludi cartelle con /traffic-light-review:config."
    )}


def _restart(repo: baseline.Repo, config: Config, files: List[Path]) -> None:
    # Come al primo avvio: indice separato da capo (i suoi blob potrebbero essere stati potati insieme al tree).
    baseline.clear(repo)
    tree = baseline.update_tree(repo, files)
    baseline.write_baseline(repo, tree)
    baseline.write_last_tree(repo, tree)
    _write_fingerprint(repo.state, config)


def _fmt_count(n):
    return "bin" if n is None else str(n)


def format_summary(
    summary: DiffSummary, pre: PrefilterResult, cfg: ReportConfig, triage: Optional[Triage] = None
) -> str:
    n = len(summary.files)
    label = "file modificato" if n == 1 else "file modificati"
    lines = [f"traffic-light-review: {n} {label} dall'ultima review (+{summary.added} / −{summary.deleted})"]
    # L'avviso di diff troncato va in cima: un verdetto su un diff parziale non deve passare inosservato.
    if triage is not None and triage.warning:
        lines.insert(0, triage.warning)
    ignored = {id(f) for f in pre.ignored}
    for f in summary.files[:cfg.max_listed_files]:
        tag = "  (ignorato)" if id(f) in ignored else ""
        lines.append(f"  {f.status} {f.path}  +{_fmt_count(f.added)} −{_fmt_count(f.deleted)}{tag}")
    if n > cfg.max_listed_files:
        lines.append(f"  … e altri {n - cfg.max_listed_files}")
    lines.append(f"Pre-filtro: {pre.reason} → {'triage Jev' if pre.passed else 'nessun triage'}")
    if triage is not None:
        lines += triage.lines
    return "\n".join(lines)


def _truncation_warning(payload: dict) -> Optional[str]:
    omitted, truncated = payload["omitted_files"], payload["truncated_files"]
    if not omitted and not truncated:
        return None
    return (f"⚠️ Jev ha visto solo parte del diff: {omitted} file omessi, {truncated} troncati. "
            f"Il verdetto riguarda solo la parte inviata.")


@dataclasses.dataclass
class Triage:
    warning: Optional[str]  # diff troncato per Jev
    lines: List[str]  # righe per il riepilogo
    relevant: Dict[str, List[str]]  # check critico → file con un indizio
    decision: Optional[Decision] = None  # None se Jev non risponde
    answers: Optional[jev.JevAnswers] = None


def _triage(
    state: Path, config: Config, pre: PrefilterResult, file_hunks: Optional[Dict[str, str]], at_cap: bool
) -> Triage:
    """Chiede a Jev e decide se consigliare la review."""
    # File rilevanti per ogni check critico: servono al troncamento, al log e al focus del subagent.
    relevant = jev.critical_files(pre.counted, file_hunks or {})
    payload = jev.build_state(pre.counted, file_hunks, relevant)
    warning = _truncation_warning(payload)
    try:
        answers = jev.ask(payload, config.jev)
    except jev.JevUnavailable as exc:
        return Triage(warning, [f"Jev non disponibile: {exc}"], relevant)
    decision = decide(answers, config.decision)
    cost = jev.cost_usd(answers)
    record = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "files": len(pre.counted),
        "lines": pre.lines,
        "answers": dataclasses.asdict(answers),
        "decision": dataclasses.asdict(decision),
        "omitted_files": payload["omitted_files"],
        "truncated_files": payload["truncated_files"],
        "secrets_redacted": payload["secrets_redacted"],
        # Per check critico, quanti file hanno un indizio (escalation_patterns): serve a simulare
        # offline una regola "incerto conta solo con un indizio" quando si tarano le soglie.
        "evidence": {name: len(paths) for name, paths in relevant.items()},
        "cost_usd": round(cost, 6),
        # La baseline riparte senza review: righe accumulate al tetto prefilter.max_lines.
        "cap_reset": at_cap,
    }
    (state / LAST_TRIAGE_FILE).write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _append_log(state / TRIAGE_LOG_FILE, record)

    lines = [describe(decision), uncertainty_line(decision)]
    if config.report.checks == "all":
        lines += all_checks(answers, decision)
    tokens = answers.usage.get("input_tokens")
    if tokens:
        lines.append(f"Jev: {tokens} token, ${cost:.4f}")
    return Triage(warning, lines, relevant, decision, answers)


def _append_log(path: Path, record: dict) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    lines = lines[-(TRIAGE_LOG_MAX_LINES - 1):] + [json.dumps(record, ensure_ascii=False)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def block_reason(decision: Decision, pre: PrefilterResult, pending_dir: Path) -> str:
    """Istruzioni per Claude: proporre la review e avviarla solo se l'utente conferma."""
    added = sum(f.added or 0 for f in pre.counted)
    deleted = sum(f.deleted or 0 for f in pre.counted)
    question = (
        f"Ho rilevato modifiche su {len(pre.counted)} file dall'ultima review (+{added} / −{deleted}): {'; '.join(reasons(decision))}. "
        f"Consiglio una review: la avvio?"
    )
    return (
        f"traffic-light-review: Jev consiglia una review delle modifiche dall'ultima review. "
        f"Fai all'utente solo questa domanda, senza aggiungere altro, e fermati in attesa della risposta: «{question}» "
        f"Se l'utente conferma, lancia il subagent `{REVIEW_AGENT}` in primo piano (non in background) con questo prompt, "
        f"compilando le ultime tre righe dalla conversazione (una riga ciascuna, al massimo circa 200 caratteri, niente codice né segreti): "
        f"«Review del diff in {pending_dir}/\n"
        f"Obiettivo: <cosa ha chiesto l'utente nei turni coperti dal diff, oppure \"non noto\">\n"
        f"Richiesto dall'utente: <scelte che l'utente ha chiesto o approvato esplicitamente, oppure \"nessuna\">\n"
        f"Fuori scope: <cosa non è stato toccato apposta, oppure \"nessuno\">». "
        f"In \"Richiesto dall'utente\" non mettere le scelte che hai fatto tu di tua iniziativa e non giustificarle. "
        f"Poi riporta il verdetto del subagent senza riassumerlo né ampliarlo. "
        f"Se l'utente rifiuta, non avviare nessuna review e prosegui normalmente. "
        f"Non correggere niente di tua iniziativa: le correzioni le decide l'utente."
    )


def run(hook_input: dict) -> dict:
    """Esegue un turno del triage. Restituisce il JSON da stampare su stdout (vuoto = silenzio)."""
    # Se Claude sta già continuando per via di un nostro block, non rientrare: eviterebbe il loop.
    if hook_input.get("stop_hook_active"):
        return {}

    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or hook_input.get("cwd") or os.getcwd()).resolve()
    repo = baseline.find_repo(root)
    if repo is None:
        return {"systemMessage": baseline.NOT_A_REPO_MESSAGE}
    state = repo.state
    state.mkdir(parents=True, exist_ok=True)
    # Una review proposta vale solo fino allo Stop successivo: a quel punto è stata fatta o rifiutata.
    # Prima della config: con una config non valida il pending vecchio non deve restare.
    pending.clear(state)
    config: Config = load_config(repo.config_file)

    try:
        files = list(iter_code_files(root, config.files))
    except TooManyFiles:
        return _too_many_files(repo, config)
    (state / TOO_MANY_FILE).unlink(missing_ok=True)

    # Primo avvio, o baseline potata da git gc: nessun tree con cui confrontare, lo creiamo e basta.
    tree_prev = baseline.read_baseline(repo)
    if tree_prev is None or not baseline.tree_exists(repo, tree_prev):
        _restart(repo, config, files)
        return {"systemMessage": f"traffic-light-review: attivo su {len(files)} file"}

    # Se cambiano i filtri, il diff mostrerebbe file aggiunti o tolti che nessuno ha toccato.
    if _read_fingerprint(state) != _fingerprint(config):
        _restart(repo, config, files)
        return {"systemMessage": f"traffic-light-review: filtri cambiati, riparto da {len(files)} file"}

    # Stesso tree dell'ultimo Stop: nessun file di codice cambiato, niente git diff né Jev.
    tree_now = baseline.update_tree(repo, files)
    if tree_now == baseline.read_last_tree(repo):
        return {}
    # Prima del diff e di Jev: se falliscono, un turno senza modifiche non riprova.
    baseline.write_last_tree(repo, tree_now)
    # Modifiche annullate fino alla baseline.
    if tree_now == tree_prev:
        return {}

    # La baseline resta all'ultima review: il diff accumula tutti i turni non ancora rivisti.
    summary = numstat(root, tree_prev, tree_now)
    if not summary.files:
        return {}
    pre = run_prefilter(summary, config.prefilter)
    # Gli hunk servono solo se si chiama Jev.
    file_hunks = hunks(root, tree_prev, tree_now, summary) if pre.passed else None
    at_cap = pre.lines >= config.prefilter.max_lines

    try:
        triage = _triage(state, config, pre, file_hunks, at_cap) if pre.passed else None
        decision = triage.decision if triage is not None else None

        output = {}
        no_review = decision is not None and not decision.review
        mode = config.report.summary
        if (
            mode == "always"
            or (mode == "unless_no_review" and not no_review)
            or (mode == "above_threshold" and pre.passed)
        ):
            output["systemMessage"] = format_summary(summary, pre, config.report, triage)
        if decision is not None and decision.review:
            # Review proposta: accettata o rifiutata, il diff successivo riparte da qui.
            baseline.write_baseline(repo, tree_now)
            pending.write(root, state, decision, triage.answers, pre.counted, file_hunks, triage.relevant)
            # Claude non chiude il turno: propone la review e la avvia solo se l'utente conferma.
            output["decision"] = "block"
            output["reason"] = block_reason(decision, pre, state / pending.PENDING_DIR)
        return output
    finally:
        # Al tetto la baseline riparte in silenzio dopo il triage, anche se Jev non risponde o manca la chiave.
        if at_cap:
            baseline.write_baseline(repo, tree_now)


# Exit code se manca TYPESAFE_API_KEY: Claude Code mostra stderr e ignora stdout (niente riepilogo in quel turno).
MISSING_KEY_EXIT_CODE = 4


def main() -> int:
    try:
        # Byte e UTF-8 espliciti: su Windows la codifica di default di stdin e stdout non è UTF-8.
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        hook_input = json.loads(raw) if raw.strip() else {}
        output = run(hook_input)
    except jev.JevKeyMissing as exc:
        # last_tree è già scritto; la baseline si è mossa solo se il turno era al tetto (finally in run()).
        sys.stderr.buffer.write(f"traffic-light-review: {exc}\n".encode("utf-8"))
        sys.stderr.flush()
        return MISSING_KEY_EXIT_CODE
    except ConfigError as exc:
        output = {"systemMessage": f"traffic-light-review: config non valida: {exc}"}
    except Exception as exc:  # l'hook non deve mai rompere la sessione
        output = {"systemMessage": f"traffic-light-review: errore interno ({type(exc).__name__})"}
    if output:
        sys.stdout.write(json.dumps(output) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
