from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class PiiType(StrEnum):
    IBAN = "IBAN"
    STEUER_ID = "STEUER_ID"
    EMAIL = "EMAIL"
    PHONE = "PHONE"
    ADDRESS = "ADDRESS"
    POSTAL_CODE = "POSTAL_CODE"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    PERSON = "PERSON"
    LOCATION = "LOCATION"


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open character range `[start, end)` that one detector believes is PII."""

    start: int
    end: int
    type: PiiType
    detector: str
    # Higher wins when spans of different types overlap. Checksum-validated
    # matches outrank plain patterns, which outrank the statistical model.
    priority: int

    @property
    def length(self) -> int:
        return self.end - self.start

    def overlaps(self, other: "Span") -> bool:
        return self.start < other.end and other.start < self.end


class Detector(Protocol):
    name: str

    def detect(self, text: str) -> list[Span]: ...
