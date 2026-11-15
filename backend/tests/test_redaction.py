import pytest

from app.redaction.engine import (
    PlaceholderVault,
    RedactionEngine,
    build_engine,
    resolve_overlaps,
)
from app.redaction.types import PiiType, Span
from app.redaction.validators import iban_is_valid, steuer_id_check_digit, steuer_id_is_valid

VALID_IBAN = "DE89370400440532013000"
VALID_STEUER_ID = "86095742719"


@pytest.fixture(scope="module")
def patterns() -> RedactionEngine:
    return build_engine(use_ner=False)


@pytest.fixture(scope="module")
def full() -> RedactionEngine:
    return build_engine(use_ner=True)


def found(engine: RedactionEngine, text: str) -> list[tuple[PiiType, str]]:
    return [(span.type, text[span.start : span.end]) for span in engine.detect(text)]


# --- validators --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (VALID_IBAN, True),
        ("AT611904300234573201", True),
        ("GB82WEST12345698765432", True),  # country without a length entry: checksum only
        ("DE89370400440532013001", False),  # wrong checksum
        ("DE8937040044053201300", False),  # too short for Germany
        ("DE89 3704", False),
        ("DE8937040044053201300ä", False),
    ],
)
def test_iban_checksum(value: str, valid: bool) -> None:
    assert iban_is_valid(value) is valid


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (VALID_STEUER_ID, True),
        ("86095742718", False),  # wrong check digit
        ("01234567899", False),  # leading zero
        ("12345678903", False),  # no repeated digit among the first ten
        ("11223344556", False),  # several repeated digits
        ("8609574271", False),
        ("8609574271x", False),
    ],
)
def test_steuer_id_checksum(value: str, valid: bool) -> None:
    assert steuer_id_is_valid(value) is valid


def test_steuer_id_check_digit_matches_published_example() -> None:
    assert steuer_id_check_digit(VALID_STEUER_ID[:10]) == int(VALID_STEUER_ID[10])


# --- pattern detectors -------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Konto {VALID_IBAN} bitte.", [(PiiType.IBAN, VALID_IBAN)]),
        (
            "IBAN DE89 3704 0044 0532 0130 00 AB Bank",
            [(PiiType.IBAN, "DE89 3704 0044 0532 0130 00")],
        ),
        ("Steuer-ID 86 095 742 719.", [(PiiType.STEUER_ID, "86 095 742 719")]),
        ("Mail an max.mustermann@example.de.", [(PiiType.EMAIL, "max.mustermann@example.de")]),
        ("Tel. 030 12345678", [(PiiType.PHONE, "030 12345678")]),
        ("Mobil +49 171 2345678!", [(PiiType.PHONE, "+49 171 2345678")]),
        ("Fax 0221/987654", [(PiiType.PHONE, "0221/987654")]),
        ("geb. 12.03.1985 in Kiel", [(PiiType.DATE_OF_BIRTH, "12.03.1985")]),
        ("Geburtsdatum: 01.01.1970", [(PiiType.DATE_OF_BIRTH, "01.01.1970")]),
        ("Hauptstraße 12a", [(PiiType.ADDRESS, "Hauptstraße 12a")]),
        ("Berliner Straße 7", [(PiiType.ADDRESS, "Berliner Straße 7")]),
        ("Mozartstr. 3", [(PiiType.ADDRESS, "Mozartstr. 3")]),
        ("Am Markt 5", [(PiiType.ADDRESS, "Am Markt 5")]),
        ("66111 Saarbrücken", [(PiiType.POSTAL_CODE, "66111 Saarbrücken")]),
        ("60311 Frankfurt am Main", [(PiiType.POSTAL_CODE, "60311 Frankfurt am Main")]),
        ("Frau Yılmaz ruft an", [(PiiType.PERSON, "Yılmaz")]),
        ("Herrn Dr. Meyer-Landrut", [(PiiType.PERSON, "Meyer-Landrut")]),
    ],
)
def test_pattern_detects(
    patterns: RedactionEngine, text: str, expected: list[tuple[PiiType, str]]
) -> None:
    assert found(patterns, text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Bestellnummer 48291037465 wurde versendet.",  # 11 digits, checksum fails
        "Die IBAN DE00 0000 0000 0000 0000 00 ist ungültig.",
        "Der Bericht vom 12.03.2024 hat 204 Seiten.",  # a date, but not a date of birth
        "Bitte liefern Sie 12345 Stück.",
        "Am Montag 5 Termine, Umsatz 48200 Euro.",
        "Frau und Herr waren nicht da.",
        "Version 0.5 kostet 0,99 Euro.",
        "",
    ],
)
def test_pattern_ignores(patterns: RedactionEngine, text: str) -> None:
    assert found(patterns, text) == []


# --- overlap resolution ------------------------------------------------------


def test_checksum_match_beats_overlapping_phone_guess(patterns: RedactionEngine) -> None:
    # The tail of an IBAN looks like a phone number; only the IBAN may survive.
    text = "DE89 3704 0044 0532 0130 00"
    assert found(patterns, text) == [(PiiType.IBAN, text)]


def test_same_type_overlaps_are_merged() -> None:
    spans = [
        Span(5, 9, PiiType.PERSON, "salutation_name", 65),
        Span(5, 17, PiiType.PERSON, "spacy_ner", 50),
    ]
    assert resolve_overlaps(spans) == [Span(5, 17, PiiType.PERSON, "salutation_name", 65)]


def test_higher_priority_wins_then_longer_span() -> None:
    low = Span(0, 10, PiiType.LOCATION, "a", 40)
    high = Span(5, 8, PiiType.EMAIL, "b", 90)
    apart = Span(20, 25, PiiType.PHONE, "c", 70)
    assert resolve_overlaps([low, high, apart]) == [high, apart]

    short = Span(0, 4, PiiType.PHONE, "a", 70)
    long = Span(0, 9, PiiType.ADDRESS, "b", 70)
    assert resolve_overlaps([short, long]) == [long]


# --- placeholders and round trip ---------------------------------------------


def test_redact_and_rehydrate_round_trip(patterns: RedactionEngine) -> None:
    text = f"Frau Schmidt ({VALID_IBAN}) schreibt von a.schmidt@example.de aus 10115 Berlin."

    result = patterns.redact(text)

    assert result.text == "Frau <PERSON_1> (<IBAN_1>) schreibt von <EMAIL_1> aus <POSTAL_CODE_1>."
    assert VALID_IBAN not in result.text
    assert result.vault.rehydrate(result.text) == text
    assert [text[e.start : e.end] for e in result.entities] == list(result.vault.mapping.values())


def test_same_value_gets_same_placeholder_across_messages(patterns: RedactionEngine) -> None:
    vault = PlaceholderVault()
    first = patterns.redact(f"Konto {VALID_IBAN}", vault=vault)
    second = patterns.redact(
        "Nochmal: DE89 3704 0044 0532 0130 00 und b@example.de und a@example.de", vault=vault
    )
    third = patterns.redact("Kontakt A@Example.de", vault=vault)

    assert first.text == "Konto <IBAN_1>"
    assert second.text == "Nochmal: <IBAN_1> und <EMAIL_1> und <EMAIL_2>"
    assert third.text == "Kontakt <EMAIL_2>"
    # Rehydration restores the spelling that was seen first.
    assert vault.rehydrate("<IBAN_1>") == VALID_IBAN


def test_only_enabled_types_are_redacted(patterns: RedactionEngine) -> None:
    text = f"{VALID_IBAN} oder a@example.de"
    result = patterns.redact(text, enabled={PiiType.EMAIL})
    assert result.text == f"{VALID_IBAN} oder <EMAIL_1>"


def test_unknown_placeholders_survive_rehydration() -> None:
    vault = PlaceholderVault()
    vault.placeholder_for(PiiType.PERSON, "Anna")
    assert (
        vault.rehydrate("<PERSON_1> trifft <PERSON_7> und <b>") == "Anna trifft <PERSON_7> und <b>"
    )


def test_vault_survives_serialisation() -> None:
    vault = PlaceholderVault()
    vault.placeholder_for(PiiType.PERSON, "Anna Schmidt")
    vault.placeholder_for(PiiType.PERSON, "Jonas Weber")

    restored = PlaceholderVault.from_mapping(vault.mapping)

    assert restored.mapping == vault.mapping
    assert restored.placeholder_for(PiiType.PERSON, "anna  schmidt") == "<PERSON_1>"
    assert restored.placeholder_for(PiiType.PERSON, "Neu") == "<PERSON_3>"
    with pytest.raises(ValueError, match="not a placeholder"):
        PlaceholderVault.from_mapping({"PERSON_1": "x"})


def test_text_without_pii_is_unchanged(patterns: RedactionEngine) -> None:
    text = "Das Meeting dauert 45 Minuten."
    result = patterns.redact(text)
    assert (result.text, result.entities) == (text, ())


# --- statistical detector ----------------------------------------------------


def test_ner_finds_names_and_places_without_fixed_format(full: RedactionEngine) -> None:
    text = "Angela Merkel ist gestern nach Hamburg gereist."
    assert found(full, text) == [
        (PiiType.PERSON, "Angela Merkel"),
        (PiiType.LOCATION, "Hamburg"),
    ]


def test_salutation_and_ner_spans_merge_into_full_name(full: RedactionEngine) -> None:
    text = "Bitte informieren Sie Frau Angela Merkel über den Termin."
    assert found(full, text) == [(PiiType.PERSON, "Angela Merkel")]


def test_ner_span_keeps_name_followed_by_colon(full: RedactionEngine) -> None:
    # Regression: a label filter once dropped real names in front of a colon.
    text = "Zusammenfassung des Gesprächs mit Angela Merkel: Die Kundin wünscht eine Kündigung."
    assert (PiiType.PERSON, "Angela Merkel") in found(full, text)


# --- memory behaviour of the statistical detector ----------------------------


def test_chunks_cover_text_exactly_and_respect_the_limit() -> None:
    from app.redaction.ner import chunks

    text = ("Erster Satz. Zweiter Satz mit mehr Wörtern.\n" * 40) + "x" * 300
    pieces = list(chunks(text, limit=100))

    assert "".join(piece for _, piece in pieces) == text
    assert all(len(piece) <= 100 for _, piece in pieces)
    assert all(text[offset : offset + len(piece)] == piece for offset, piece in pieces)
    # Wherever a line break is available, the cut is made there, not mid-sentence.
    assert all(piece.endswith("\n") for _, piece in pieces if "Satz" in piece)
    assert list(chunks("")) == []


def test_ner_offsets_are_correct_beyond_the_first_chunk(full: RedactionEngine) -> None:
    filler = "Das ist ein ganz gewöhnlicher Satz ohne Besonderheiten.\n" * 120
    text = filler + "Angela Merkel reist nach Hamburg."
    assert len(filler) > 4000

    assert found(full, text) == [(PiiType.PERSON, "Angela Merkel"), (PiiType.LOCATION, "Hamburg")]


def test_vocabulary_does_not_grow_with_traffic(full: RedactionEngine) -> None:
    from app.redaction.ner import load_model

    strings = load_model().vocab.strings
    assert len(strings) < 5_000  # pruned from about 460k at load time
    size_before = len(strings)

    for index in range(200):
        full.detect(f"Unbekanntes Wort Xq{index}zzy und Qw{index}rtz in Satz {index}.")

    assert len(strings) == size_before
