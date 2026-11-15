import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from app.redaction.detectors import pattern_detectors
from app.redaction.types import Detector, PiiType, Span

PLACEHOLDER = re.compile(r"<([A-Z_]+)_(\d+)>")

# Formats where spacing and separators do not change the identity of the value.
_COMPACT_TYPES = {PiiType.IBAN, PiiType.STEUER_ID, PiiType.PHONE}


def _identity(pii_type: PiiType, value: str) -> str:
    if pii_type in _COMPACT_TYPES:
        return re.sub(r"[\s\-/]", "", value).upper()
    return " ".join(value.split()).casefold()


class PlaceholderVault:
    """Two-way mapping between placeholders and original values.

    The same value always gets the same placeholder, so the LLM can still tell
    that two mentions refer to one person. Holds raw PII: never log or persist
    it in Postgres.
    """

    def __init__(self) -> None:
        self._originals: dict[str, str] = {}
        self._by_identity: dict[tuple[PiiType, str], str] = {}
        self._counters: dict[PiiType, int] = {}

    @classmethod
    def from_mapping(cls, mapping: dict[str, str]) -> "PlaceholderVault":
        vault = cls()
        for placeholder, original in mapping.items():
            match = PLACEHOLDER.fullmatch(placeholder)
            if match is None:
                raise ValueError("not a placeholder")
            pii_type, number = PiiType(match.group(1)), int(match.group(2))
            vault._originals[placeholder] = original
            vault._by_identity[(pii_type, _identity(pii_type, original))] = placeholder
            vault._counters[pii_type] = max(vault._counters.get(pii_type, 0), number)
        return vault

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._originals)

    def placeholder_for(self, pii_type: PiiType, value: str) -> str:
        key = (pii_type, _identity(pii_type, value))
        existing = self._by_identity.get(key)
        if existing is not None:
            return existing
        number = self._counters.get(pii_type, 0) + 1
        self._counters[pii_type] = number
        placeholder = f"<{pii_type.value}_{number}>"
        self._by_identity[key] = placeholder
        self._originals[placeholder] = value
        return placeholder

    def rehydrate(self, text: str) -> str:
        """Put original values back. Unknown placeholders are left untouched."""
        return PLACEHOLDER.sub(
            lambda match: self._originals.get(match.group(), match.group()), text
        )


@dataclass(frozen=True, slots=True)
class Entity:
    type: PiiType
    start: int
    end: int
    placeholder: str
    detector: str


@dataclass(frozen=True, slots=True)
class RedactionResult:
    text: str
    entities: tuple[Entity, ...]
    vault: PlaceholderVault


def _merge_same_type(spans: Sequence[Span]) -> list[Span]:
    """Union overlapping spans of one type, e.g. salutation "Anna" + NER "Anna Schmidt"."""
    merged: list[Span] = []
    for span in sorted(spans, key=lambda item: (item.type, item.start, -item.end)):
        last = merged[-1] if merged else None
        if last is not None and last.type == span.type and span.start < last.end:
            stronger = last if last.priority >= span.priority else span
            merged[-1] = Span(
                last.start, max(last.end, span.end), last.type, stronger.detector, stronger.priority
            )
        else:
            merged.append(span)
    return merged


def resolve_overlaps(spans: Sequence[Span]) -> list[Span]:
    """Return non-overlapping spans in text order.

    Same-type overlaps are merged. Across types the higher priority wins, then
    the longer span, then the earlier one.
    """
    accepted: list[Span] = []
    ranked = sorted(_merge_same_type(spans), key=lambda s: (-s.priority, -s.length, s.start))
    for span in ranked:
        if not any(span.overlaps(other) for other in accepted):
            accepted.append(span)
    return sorted(accepted, key=lambda s: s.start)


class RedactionEngine:
    def __init__(self, detectors: Sequence[Detector]) -> None:
        self._detectors = list(detectors)

    def detect(self, text: str, enabled: Collection[PiiType] | None = None) -> list[Span]:
        spans = [
            span
            for detector in self._detectors
            for span in detector.detect(text)
            if enabled is None or span.type in enabled
        ]
        return resolve_overlaps(spans)

    def redact(
        self,
        text: str,
        *,
        vault: PlaceholderVault | None = None,
        enabled: Collection[PiiType] | None = None,
    ) -> RedactionResult:
        """Replace PII with placeholders. Pass the same vault for every message of a request."""
        vault = vault if vault is not None else PlaceholderVault()
        pieces: list[str] = []
        entities: list[Entity] = []
        cursor = 0
        for span in self.detect(text, enabled):
            placeholder = vault.placeholder_for(span.type, text[span.start : span.end])
            pieces += [text[cursor : span.start], placeholder]
            entities.append(Entity(span.type, span.start, span.end, placeholder, span.detector))
            cursor = span.end
        pieces.append(text[cursor:])
        return RedactionResult("".join(pieces), tuple(entities), vault)


def build_engine(*, use_ner: bool) -> RedactionEngine:
    detectors = pattern_detectors()
    if use_ner:
        # Imported here so pattern-only use never pays for loading spaCy.
        from app.redaction.ner import NerDetector, load_model

        detectors.append(NerDetector(load_model()))
    return RedactionEngine(detectors)
