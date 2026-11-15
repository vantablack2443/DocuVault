import pytest

from app.redaction.engine import build_engine
from app.redaction.types import PiiType, Span
from app.redaction.validators import iban_is_valid, steuer_id_is_valid
from benchmark.__main__ import main
from benchmark.corpus import Document, GoldSpan, build_corpus
from benchmark.score import evaluate, to_markdown, violations


def test_corpus_is_deterministic_and_well_formed() -> None:
    corpus = build_corpus()
    assert corpus == build_corpus()
    assert corpus != build_corpus(held_out=True)
    for document in corpus + build_corpus(held_out=True):
        for span in document.spans:
            value = document.text[span.start : span.end]
            assert value and value == value.strip()
            if span.type is PiiType.IBAN:
                assert iban_is_valid(value.replace(" ", ""))
            if span.type is PiiType.STEUER_ID:
                assert steuer_id_is_valid(value.replace(" ", ""))


def test_scoring_counts_exact_matches_and_leaked_characters() -> None:
    document = Document(
        "Anna wohnt in Kiel.", (GoldSpan(0, 4, PiiType.PERSON), GoldSpan(14, 18, PiiType.LOCATION))
    )

    def predict(_: str) -> list[Span]:
        return [
            Span(0, 4, PiiType.PERSON, "t", 1),  # exact
            Span(14, 16, PiiType.LOCATION, "t", 1),  # right type, wrong boundary
            Span(5, 10, PiiType.EMAIL, "t", 1),  # spurious
        ]

    report = evaluate([document], predict)

    person, location, email = (
        report.per_type[t] for t in (PiiType.PERSON, PiiType.LOCATION, PiiType.EMAIL)
    )
    assert (person.precision, person.recall, person.f1) == (1.0, 1.0, 1.0)
    assert (location.true_positives, location.false_positives, location.false_negatives) == (
        0,
        1,
        1,
    )
    assert (location.precision, location.recall, location.f1) == (0.0, 0.0, 0.0)
    assert (email.precision, email.recall, email.support) == (0.0, 1.0, 0)
    assert (report.pii_characters, report.leaked_characters) == (8, 2)
    assert report.leak_rate == 0.25
    assert "| `PERSON` | 1.000 | 1.000 | 1.000 | 1 |" in to_markdown(report)


def test_threshold_gate_reports_each_violation() -> None:
    report = evaluate([Document("Anna", (GoldSpan(0, 4, PiiType.PERSON),))], lambda _: [])
    assert violations(report, {"max_leak_rate": 1.0, "per_type": {}}) == []
    assert violations(report, {"max_leak_rate": 0.1, "per_type": {"PERSON": {"recall": 0.9}}}) == [
        "leak rate 1.0000 > allowed 0.1000",
        "PERSON recall 0.000 < required 0.900",
    ]


def test_patterns_alone_never_miss_checksummed_identifiers() -> None:
    report = evaluate(build_corpus(), build_engine(use_ner=False).detect)
    for pii_type in (PiiType.IBAN, PiiType.STEUER_ID, PiiType.EMAIL):
        assert report.per_type[pii_type].recall == 1.0
        assert report.per_type[pii_type].precision == 1.0


def test_benchmark_meets_stored_thresholds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "Held-out templates" in output
    assert "Leak rate" in output
