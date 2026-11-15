"""Deterministic detectors: regular expressions, with checksums where the format has one."""

import re
from collections.abc import Callable

from app.redaction.types import Detector, PiiType, Span
from app.redaction.validators import iban_is_valid, steuer_id_is_valid

_UPPER = "A-ZÄÖÜ"
_LOWER = "a-zäöüß"
# Any Unicode letters, so names such as "Yılmaz" or "Çelik" are covered.
_NAME = r"[^\W\d_]+(?:-[^\W\d_]+)?"
_NOT_A_PLACE = (
    "Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag|Sonntag|Januar|Februar|März|April|Mai"
    "|Juni|Juli|August|September|Oktober|November|Dezember|Ende|Anfang"
)


class RegexDetector:
    """One pattern, one PII type. `group` selects the part of the match to redact."""

    def __init__(
        self,
        name: str,
        pii_type: PiiType,
        pattern: str,
        *,
        priority: int,
        group: int = 0,
        flags: int = 0,
        validator: Callable[[str], bool] | None = None,
    ) -> None:
        self.name = name
        self._type = pii_type
        self._pattern = re.compile(pattern, flags)
        self._priority = priority
        self._group = group
        self._validator = validator

    def detect(self, text: str) -> list[Span]:
        spans = []
        for match in self._pattern.finditer(text):
            if self._validator is not None and not self._validator(match.group(self._group)):
                continue
            spans.append(
                Span(
                    match.start(self._group),
                    match.end(self._group),
                    self._type,
                    self.name,
                    self._priority,
                )
            )
        return spans


class IbanDetector:
    """IBANs, compact or in groups of four.

    The candidate pattern is deliberately greedy, so it can swallow a following
    upper-case token ("… 3000 AB"). Trailing groups are dropped until the
    checksum passes; if it never does, the candidate is not an IBAN.
    """

    name = "iban_checksum"
    _candidate = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{1,4}){3,8}\b")

    def detect(self, text: str) -> list[Span]:
        spans = []
        for match in self._candidate.finditer(text):
            candidate = match.group()
            boundaries = [index for index, char in enumerate(candidate) if char == " "]
            for end in [len(candidate), *reversed(boundaries)]:
                if iban_is_valid(candidate[:end].replace(" ", "")):
                    spans.append(
                        Span(match.start(), match.start() + end, PiiType.IBAN, self.name, 100)
                    )
                    break
        return spans


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _plausible_phone(value: str) -> bool:
    return 8 <= len(_digits(value)) <= 15


# Nouns that commonly follow a five-digit quantity and would look like "PLZ Ort".
_NOT_A_TOWN = {"Stück", "Euro", "Einheiten", "Teile", "Mal", "Uhr"}


def _plausible_postal_code(value: str) -> bool:
    return value.split(maxsplit=1)[1] not in _NOT_A_TOWN


def pattern_detectors() -> list[Detector]:
    return [
        IbanDetector(),
        RegexDetector(
            "steuer_id_checksum",
            PiiType.STEUER_ID,
            r"(?<!\d)\d{2} ?\d{3} ?\d{3} ?\d{3}(?!\d)",
            priority=100,
            validator=lambda value: steuer_id_is_valid(_digits(value)),
        ),
        RegexDetector(
            "email",
            PiiType.EMAIL,
            r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}",
            priority=90,
        ),
        RegexDetector(
            "phone_de",
            PiiType.PHONE,
            r"(?<![\w+])(?:\+49[ \-]?|0049[ \-]?|0)[1-9]\d{1,4}(?:[ \-/]?\d{2,}){1,4}(?!\w)",
            priority=70,
            validator=_plausible_phone,
        ),
        RegexDetector(
            "date_of_birth",
            PiiType.DATE_OF_BIRTH,
            r"(?:geb\.|geboren(?: am)?|Geburtsdatum:?)\s*(\d{1,2}\.\d{1,2}\.\d{4})",
            priority=80,
            group=1,
            flags=re.IGNORECASE,
        ),
        RegexDetector(
            "street_address",
            PiiType.ADDRESS,
            rf"\b(?:[{_UPPER}][{_LOWER}]+er (?:Straße|Platz|Allee|Weg|Ring|Damm)"
            rf"|[{_UPPER}][A-Za-zäöüß\-]*"
            r"(?:straße|strasse|str\.|weg|platz|allee|gasse|ring|damm|ufer)"
            r"|(?:Am|Im|Zum|Zur|An der|Auf dem|Auf der) "
            rf"(?!(?:{_NOT_A_PLACE})\b)[{_UPPER}][{_LOWER}]+)"
            r"\s+\d{1,4}(?: ?[a-z])?\b",
            priority=60,
        ),
        RegexDetector(
            "postal_code_town",
            PiiType.POSTAL_CODE,
            rf"\b\d{{5}} [{_UPPER}][{_LOWER}]+"
            rf"(?:-[{_UPPER}][{_LOWER}]+| (?:am|an der|im|bei) [{_UPPER}][{_LOWER}]+)?\b",
            priority=55,
            validator=_plausible_postal_code,
        ),
        # Only the token directly after the salutation: German capitalises every
        # noun, so taking more would redact ordinary words ("Herr Müller Bescheid").
        # A full name is completed by merging with the NER span of the same type.
        RegexDetector(
            "salutation_name",
            PiiType.PERSON,
            rf"\b(?:Herrn|Herr|Frau)\s+(?:(?:Dr\.|Prof\.)\s+)*({_NAME})",
            priority=65,
            group=1,
            validator=lambda name: name[0].isupper(),
        ),
    ]
