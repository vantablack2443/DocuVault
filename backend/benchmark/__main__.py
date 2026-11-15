"""Redaction benchmark.

python -m benchmark            print the score table
python -m benchmark --check    also fail if a score is below benchmark/thresholds.json
python -m benchmark --no-ner   patterns only (shows what the model contributes)
"""

import argparse
import json
import sys
from pathlib import Path

from app.redaction.engine import build_engine
from benchmark.corpus import build_corpus
from benchmark.score import evaluate, to_markdown, violations

THRESHOLDS = Path(__file__).with_name("thresholds.json")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="benchmark", description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail when below thresholds")
    parser.add_argument("--no-ner", action="store_true", help="disable the spaCy detector")
    arguments = parser.parse_args(argv)

    engine = build_engine(use_ner=not arguments.no_ner)
    report = evaluate(build_corpus(), engine.detect)
    held_out = evaluate(build_corpus(held_out=True), engine.detect)
    print("### Development templates (detectors were tuned on these)\n")
    print(to_markdown(report))
    print("\n### Held-out templates (never used for tuning)\n")
    print(to_markdown(held_out))

    if arguments.check:
        thresholds = json.loads(THRESHOLDS.read_text())
        problems = violations(report, thresholds["development"])
        problems += [f"held-out: {p}" for p in violations(held_out, thresholds["held_out"])]
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
