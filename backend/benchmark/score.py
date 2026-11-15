from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from app.redaction.types import PiiType, Span
from benchmark.corpus import Document

Predict = Callable[[str], Sequence[Span]]


@dataclass
class TypeScore:
    true_positives: int = 0
    false_positives: int = 0
    false_negatives: int = 0

    @property
    def precision(self) -> float:
        found = self.true_positives + self.false_positives
        return self.true_positives / found if found else 1.0

    @property
    def recall(self) -> float:
        expected = self.true_positives + self.false_negatives
        return self.true_positives / expected if expected else 1.0

    @property
    def f1(self) -> float:
        total = self.precision + self.recall
        return 2 * self.precision * self.recall / total if total else 0.0

    @property
    def support(self) -> int:
        return self.true_positives + self.false_negatives


@dataclass
class Report:
    documents: int = 0
    per_type: dict[PiiType, TypeScore] = field(default_factory=dict)
    pii_characters: int = 0
    leaked_characters: int = 0

    @property
    def leak_rate(self) -> float:
        """Share of gold PII characters that no span covered, whatever its type.

        This is the number that matters for privacy: a name labelled LOCATION is
        a scoring error but still does not reach the LLM.
        """
        return self.leaked_characters / self.pii_characters if self.pii_characters else 0.0


def evaluate(documents: Sequence[Document], predict: Predict) -> Report:
    """Strict scoring: a prediction counts only with the exact type and exact boundaries."""
    report = Report(
        documents=len(documents), per_type={pii_type: TypeScore() for pii_type in PiiType}
    )
    for document in documents:
        predicted = predict(document.text)
        gold = {(span.start, span.end, span.type) for span in document.spans}
        found = {(span.start, span.end, span.type) for span in predicted}
        for _, _, pii_type in gold & found:
            report.per_type[pii_type].true_positives += 1
        for _, _, pii_type in found - gold:
            report.per_type[pii_type].false_positives += 1
        for _, _, pii_type in gold - found:
            report.per_type[pii_type].false_negatives += 1

        covered = {index for span in predicted for index in range(span.start, span.end)}
        for span in document.spans:
            report.pii_characters += span.end - span.start
            report.leaked_characters += sum(
                1 for index in range(span.start, span.end) if index not in covered
            )
    return report


def to_markdown(report: Report) -> str:
    lines = [
        "| PII type | Precision | Recall | F1 | Gold spans |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for pii_type, score in report.per_type.items():
        lines.append(
            f"| `{pii_type.value}` | {score.precision:.3f} | {score.recall:.3f} "
            f"| {score.f1:.3f} | {score.support} |"
        )
    lines.append("")
    lines.append(
        f"Leak rate (PII characters left unredacted): **{report.leak_rate:.2%}** "
        f"over {report.documents} documents."
    )
    return "\n".join(lines)


def violations(report: Report, thresholds: dict[str, object]) -> list[str]:
    """Compare a report with stored minimums. An empty list means the gate passes."""
    problems = []
    max_leak = float(thresholds["max_leak_rate"])  # type: ignore[arg-type]
    if report.leak_rate > max_leak:
        problems.append(f"leak rate {report.leak_rate:.4f} > allowed {max_leak:.4f}")
    per_type: dict[str, dict[str, float]] = thresholds["per_type"]  # type: ignore[assignment]
    for name, minimums in per_type.items():
        score = report.per_type[PiiType(name)]
        for metric, minimum in minimums.items():
            actual = float(getattr(score, metric))
            if actual < minimum:
                problems.append(f"{name} {metric} {actual:.3f} < required {minimum:.3f}")
    return problems
