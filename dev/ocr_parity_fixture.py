# /// script
# requires-python = ">=3.10"
# ///
"""Rigenera tests/data/ocr_parity.json con la CLI `ocr` vera, dopo dev/sync_ocr_rules.py.

Solo per lo sviluppo: serve `ocr` installata alla stessa versione di plugin/traffic_light_review/ocr_rules/SOURCE.json.
Il test tests/test_rules.py confronta poi il resolver di traffic_light_review con questo risultato.

    uv run --script dev/ocr_parity_fixture.py
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "tests" / "data"
# Un path per ogni pattern di system_rules.json, più casi limite (maiuscole, cartelle, primo pattern che vince).
PATHS = """
a.py b/c.pyi n.ipynb x.js y.TSX z.mjs A.java k.kts m.go a_test.go r.rs P.php c.c h.h cc.cc hpp.hpp s.swift
objc.m mat.m hdr.m o.mm j.json package.json sub/package.json pom.xml build.gradle Cargo.toml composer.json
cfg.yaml .github/workflows/ci.yml .github/dependabot.yml .github/workflows/sub/x.yaml k8s/dep.yml
app.properties user_mapper.xml UserDao.xml other.xml t.tf v.tfvars p.proto q.graphql s.prisma f.fs e.ets
page.astro t.hbs x.pug j.jl r.R rr.r n.nix h.hs z.zig th.thrift cp.capnp ml.ml re.re v.sv d.vhd so.sol
vy.vy po.po po.pot rg.rego js.jsonnet bc.bicep ftl.ftl x.sql tool.rb Makefile Dockerfile README.md
""".split()
# File .m reali per lo sniff Objective-C / MATLAB: stanno in tests/data e il test li legge da lì.
SNIFF_FILES = ["objc.m", "mat.m", "hdr.m"]


def main() -> int:
    source = json.loads((ROOT / "plugin" / "traffic_light_review" / "ocr_rules" / "SOURCE.json").read_text())
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        for name in SNIFF_FILES:
            shutil.copy2(DATA / name, repo / name)
        out = subprocess.run(
            ["ocr", "delegate", "rule", "--format", "json", *PATHS], cwd=repo, capture_output=True, check=True
        ).stdout
    files = {
        f: {"pattern": g["pattern"], "rule_sha256": hashlib.sha256(g["rule"].rstrip("\n").encode()).hexdigest()}
        for g in json.loads(out)["groups"]
        for f in g["files"]
    }
    fixture = {"ocr_version": source["tag"], "files": dict(sorted(files.items()))}
    (DATA / "ocr_parity.json").write_text(json.dumps(fixture, indent=1) + "\n", encoding="utf-8")
    print(f"{len(files)} path, regole OCR {source['tag']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
