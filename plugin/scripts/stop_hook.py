# /// script
# requires-python = ">=3.10"
# dependencies = [
#     "pyyaml>=6",
#     "typesafe-sdk>=0.7,<0.8",
# ]
# ///
"""Ingresso dello Stop hook, da lanciare con: uv run --script plugin/scripts/stop_hook.py

uv legge le dipendenze dall'header qui sopra (PEP 723) e le installa in un ambiente in cache.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from traffic_light_review.stop_hook import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
