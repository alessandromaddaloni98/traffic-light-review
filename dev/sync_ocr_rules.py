# /// script
# requires-python = ">=3.10"
# ///
"""Copia le regole di review di Open Code Review (Apache-2.0) in plugin/traffic_light_review/ocr_rules/.

Solo per lo sviluppo del plugin: chi lo usa trova le regole già nel repo. Uso:

    uv run --script dev/sync_ocr_rules.py            # ultima release
    uv run --script dev/sync_ocr_rules.py v1.12.9    # tag preciso

Scarica system_rules.json, i file di rule_docs/ che cita (più objc.md, usato per i .m) e la licenza,
senza modificarli, e scrive in SOURCE.json tag e commit di provenienza.
"""

from __future__ import annotations

import json
import shutil
import sys
import urllib.request
from pathlib import Path

REPO = "alibaba/open-code-review"
RULES_PATH = "internal/config/rules"
# Regola usata dallo sniffer di OCR per i .m che sono Objective-C: non compare in system_rules.json.
EXTRA_DOCS = ["objc.md"]
DEST = Path(__file__).resolve().parent.parent / "plugin" / "traffic_light_review" / "ocr_rules"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "traffic-light-review-sync"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def _raw(ref: str, path: str) -> bytes:
    return _get(f"https://raw.githubusercontent.com/{REPO}/{ref}/{path}")


def main() -> int:
    tag = sys.argv[1] if len(sys.argv) > 1 else json.loads(
        _get(f"https://api.github.com/repos/{REPO}/releases/latest")
    )["tag_name"]
    sha = json.loads(_get(f"https://api.github.com/repos/{REPO}/commits/{tag}"))["sha"]

    system = _raw(sha, f"{RULES_PATH}/system_rules.json")
    parsed = json.loads(system)
    docs = [parsed["default_rule"], *dict.fromkeys(parsed["path_rule_map"].values()), *EXTRA_DOCS]

    tmp = DEST.with_name(DEST.name + ".tmp")
    if tmp.exists():
        shutil.rmtree(tmp)
    (tmp / "rule_docs").mkdir(parents=True)
    (tmp / "system_rules.json").write_bytes(system)
    for name in dict.fromkeys(docs):
        (tmp / "rule_docs" / name).write_bytes(_raw(sha, f"{RULES_PATH}/rule_docs/{name}"))
    (tmp / "LICENSE").write_bytes(_raw(sha, "LICENSE"))
    source = {"repository": f"https://github.com/{REPO}", "tag": tag, "commit": sha, "path": RULES_PATH}
    (tmp / "SOURCE.json").write_text(json.dumps(source, indent=2) + "\n")
    # Il README con l'attribuzione è nostro: si conserva tra una sync e l'altra.
    if (DEST / "README.md").exists():
        shutil.copy2(DEST / "README.md", tmp / "README.md")

    if DEST.exists():
        shutil.rmtree(DEST)
    tmp.rename(DEST)
    print(f"Regole OCR {tag} ({sha[:7]}): {len(set(docs))} file in {DEST}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
