"""Synthetic German corpus with gold PII spans.

Every value is generated: no real person, account or tax number appears. The
corpus is deterministic for a given seed, so scores are comparable across runs.

Limits worth knowing when reading the scores:
- Sentences come from a fixed set of templates, so the text is cleaner and more
  regular than real e-mails or tickets. Real-world recall will be lower.
- Names and towns are drawn from short lists of common German ones.
"""

import random
import re
from collections.abc import Callable
from dataclasses import dataclass

from app.redaction.types import PiiType
from app.redaction.validators import steuer_id_check_digit, steuer_id_is_valid


@dataclass(frozen=True, slots=True)
class GoldSpan:
    start: int
    end: int
    type: PiiType


@dataclass(frozen=True, slots=True)
class Document:
    text: str
    spans: tuple[GoldSpan, ...]


FIRST_NAMES = [
    "Anna", "Lukas", "Sophie", "Jonas", "Marie", "Felix", "Laura", "Maximilian", "Lena", "Paul",
    "Katharina", "Tobias", "Johanna", "Stefan", "Claudia", "Mehmet", "Aylin", "Karl-Heinz",
    "Ingrid", "Wolfgang",
]  # fmt: skip
LAST_NAMES = [
    "Müller", "Schmidt", "Schneider", "Fischer", "Weber", "Meyer", "Wagner", "Becker", "Schulz",
    "Hoffmann", "Schäfer", "Koch", "Bauer", "Richter", "Klein", "Yılmaz", "Krüger", "Lehmann",
    "Köhler", "Meyer-Landrut",
]  # fmt: skip
TOWNS = [
    "Berlin", "Hamburg", "München", "Köln", "Frankfurt am Main", "Stuttgart", "Düsseldorf",
    "Leipzig", "Dresden", "Saarbrücken", "Baden-Baden", "Freiburg im Breisgau", "Nürnberg",
    "Hannover", "Kiel",
]  # fmt: skip
STREETS = [
    "Hauptstraße", "Bahnhofstraße", "Gartenweg", "Schillerplatz", "Lindenallee", "Berliner Straße",
    "Kirchgasse", "Mozartstr.", "Am Markt", "Rosenweg", "Goethestraße", "Hamburger Allee",
]  # fmt: skip
MAIL_DOMAINS = ["example.de", "firma-beispiel.de", "mail.example.org", "beispiel-ag.com"]
QUANTITY_NOUNS = ["Stück", "Exemplare", "Besucher", "Einheiten"]

_TRANSLITERATE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss", "ı": "i"})

Generator = Callable[[random.Random], str]


def _person(rng: random.Random) -> str:
    return f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"


def _surname(rng: random.Random) -> str:
    return rng.choice(LAST_NAMES)


def _digits(rng: random.Random, count: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(count))


def _group(value: str, size: int = 4) -> str:
    return " ".join(value[index : index + size] for index in range(0, len(value), size))


def _iban(rng: random.Random) -> str:
    bban = _digits(rng, 18)
    check = 98 - int(bban + "131400") % 97  # "DE00" moved to the end: D=13, E=14
    iban = f"DE{check:02d}{bban}"
    return _group(iban) if rng.random() < 0.7 else iban


def _invalid_iban(rng: random.Random) -> str:
    valid = _iban(rng).replace(" ", "")
    wrong_check = (int(valid[2:4]) + rng.randint(1, 90)) % 97 + 1
    if f"{wrong_check:02d}" == valid[2:4]:
        wrong_check = wrong_check % 97 + 1
    return _group(f"DE{wrong_check:02d}{valid[4:]}")


def _steuer_id(rng: random.Random) -> str:
    digits = rng.sample("0123456789", 9)
    digits.append(rng.choice(digits))  # exactly one digit appears twice
    while True:
        rng.shuffle(digits)
        if digits[0] != "0":
            break
    first_ten = "".join(digits)
    value = first_ten + str(steuer_id_check_digit(first_ten))
    return f"{value[:2]} {value[2:5]} {value[5:8]} {value[8:]}" if rng.random() < 0.5 else value


def _order_number(rng: random.Random) -> str:
    while True:
        candidate = str(rng.randint(1, 9)) + _digits(rng, 10)
        if not steuer_id_is_valid(candidate):
            return candidate


def _email(rng: random.Random) -> str:
    first = rng.choice(FIRST_NAMES).lower().translate(_TRANSLITERATE)
    last = rng.choice(LAST_NAMES).lower().translate(_TRANSLITERATE)
    local = rng.choice([f"{first}.{last}", f"{first[0]}.{last}", f"{last}{rng.randint(1, 99)}"])
    return f"{local}@{rng.choice(MAIL_DOMAINS)}"


def _phone(rng: random.Random) -> str:
    area = rng.choice(["30", "40", "89", "221", "681", "711"])
    mobile = rng.choice(["151", "160", "171", "176"])
    number = str(rng.randint(1, 9)) + _digits(rng, 6)
    return rng.choice(
        [
            f"0{area} {number}",
            f"0{area}/{number}",
            f"+49 {area} {number}",
            f"+49 {mobile} {number}",
            f"0{mobile}-{number}",
            f"0049 {area} {number}",
        ]
    )


def _address(rng: random.Random) -> str:
    suffix = rng.choice(["", "", "", "a", " b"])
    return f"{rng.choice(STREETS)} {rng.randint(1, 240)}{suffix}"


def _postal_code(rng: random.Random) -> str:
    return f"{rng.randint(1001, 99998):05d} {rng.choice(TOWNS)}"


def _town(rng: random.Random) -> str:
    return rng.choice(TOWNS)


def _date(rng: random.Random, first_year: int, last_year: int) -> str:
    return f"{rng.randint(1, 28):02d}.{rng.randint(1, 12):02d}.{rng.randint(first_year, last_year)}"


def _quantity(rng: random.Random) -> str:
    return f"{rng.randint(10000, 99999)} {rng.choice(QUANTITY_NOUNS)}"


# Slot name -> (generator, gold type). `None` marks a distractor that must NOT be redacted.
SLOTS: dict[str, tuple[Generator, PiiType | None]] = {
    "PERSON": (_person, PiiType.PERSON),
    "SURNAME": (_surname, PiiType.PERSON),
    "IBAN": (_iban, PiiType.IBAN),
    "STEUER_ID": (_steuer_id, PiiType.STEUER_ID),
    "EMAIL": (_email, PiiType.EMAIL),
    "PHONE": (_phone, PiiType.PHONE),
    "ADDRESS": (_address, PiiType.ADDRESS),
    "POSTAL_CODE": (_postal_code, PiiType.POSTAL_CODE),
    "LOCATION": (_town, PiiType.LOCATION),
    "DATE_OF_BIRTH": (lambda rng: _date(rng, 1945, 2005), PiiType.DATE_OF_BIRTH),
    "INVALID_IBAN": (_invalid_iban, None),
    "ORDER_NUMBER": (_order_number, None),
    "PLAIN_DATE": (lambda rng: _date(rng, 2023, 2026), None),
    "QUANTITY": (_quantity, None),
}

TEMPLATES = [
    # Banking and tax
    "Sehr geehrte Frau {SURNAME}, bitte überweisen Sie den Betrag auf das Konto {IBAN}.",
    "Meine neue Bankverbindung lautet {IBAN}, Kontoinhaber ist {PERSON}.",
    "IBAN: {IBAN}\nVerwendungszweck: Rechnung {ORDER_NUMBER}",
    "Die Steuer-ID von {PERSON} lautet {STEUER_ID}.",
    "Für die Lohnabrechnung benötigen wir Ihre Steuerliche Identifikationsnummer ({STEUER_ID}).",
    "Steuer-ID {STEUER_ID}, IBAN {IBAN}, Rückfragen an {EMAIL}.",
    # Contact details
    "Sie erreichen {PERSON} unter {PHONE} oder per E-Mail an {EMAIL}.",
    "Bitte rufen Sie Herrn {SURNAME} unter der Nummer {PHONE} zurück.",
    "Kontakt: {EMAIL}, Tel. {PHONE}",
    "Bei Fragen wenden Sie sich an Frau Dr. {SURNAME} ({EMAIL}).",
    # Addresses
    "{PERSON} wohnt in der {ADDRESS}, {POSTAL_CODE}.",
    "Lieferadresse: {PERSON}, {ADDRESS}, {POSTAL_CODE}",
    "Bitte senden Sie die Unterlagen an {ADDRESS} in {POSTAL_CODE}.",
    "{PERSON} ist letzte Woche nach {LOCATION} gezogen.",
    "Unser Büro in {LOCATION} wird von {PERSON} geleitet.",
    # Dates of birth
    "Der Kunde {PERSON} (geb. {DATE_OF_BIRTH}) hat am {PLAIN_DATE} angerufen.",
    "Versicherte Person: {PERSON}, Geburtsdatum: {DATE_OF_BIRTH}, wohnhaft in {POSTAL_CODE}.",
    "Frau {SURNAME}, geboren am {DATE_OF_BIRTH}, beantragt eine Rückerstattung.",
    # Free text with several kinds
    "Hallo, ich bin {PERSON}. Meine Nummer ist {PHONE} und ich wohne in {LOCATION}.",
    "Können Sie mir helfen? Mein Mann {PERSON} hat die Rechnung vom {PLAIN_DATE} nicht erhalten.",
    "Zusammenfassung des Gesprächs mit {PERSON}: Die Kundin wünscht eine Kündigung zum {PLAIN_DATE}.",
    # Distractors only: nothing here is personal data
    "Die Bestellnummer {ORDER_NUMBER} wurde am {PLAIN_DATE} versendet.",
    "Bitte liefern Sie {QUANTITY} bis Freitag.",
    "Die angegebene IBAN {INVALID_IBAN} ist ungültig, bitte prüfen Sie die Eingabe.",
    "Der Bericht wurde am {PLAIN_DATE} erstellt und umfasst 204 Seiten.",
    "Das Meeting findet im Raum 3 statt, die Präsentation dauert 45 Minuten.",
    "Der Umsatz stieg im dritten Quartal um 12,5 Prozent auf 48200 Euro.",
]  # fmt: skip

# Written after the detectors were tuned on TEMPLATES and never used for tuning.
# The gap between the two score tables shows how much the rules overfit.
HELD_OUT_TEMPLATES = [
    "Guten Tag, hier spricht {PERSON} aus {LOCATION}, ich habe eine Frage zu meinem Vertrag.",
    "Wie besprochen schicke ich Ihnen die Daten: {PERSON}, {ADDRESS}, {POSTAL_CODE}, Telefon {PHONE}.",
    "Die Erstattung geht bitte an {IBAN} (Inhaberin: {PERSON}).",
    "Gestern hat {PERSON} angerufen und nach dem Stand der Reklamation {ORDER_NUMBER} gefragt.",
    "Ich bin am {DATE_OF_BIRTH} geboren und meine Steuernummer ist {STEUER_ID}.",
    "Antwort bitte an {EMAIL} oder mobil an {PHONE}, danke und viele Grüße, {PERSON}",
    "Der Mieter {PERSON} zieht zum {PLAIN_DATE} aus der Wohnung in der {ADDRESS} aus.",
    "Laut Herrn {SURNAME} wurden {QUANTITY} bestellt, geliefert wurde aber nur die Hälfte.",
    "Patientin {PERSON}, geb. {DATE_OF_BIRTH}, wohnhaft {ADDRESS}, {POSTAL_CODE}, klagt über Kopfschmerzen.",
    "Das Angebot gilt bis zum {PLAIN_DATE} und umfasst 12 Monate Wartung.",
]  # fmt: skip

_SLOT = re.compile(r"\{([A-Z_]+)\}")


def render(template: str, rng: random.Random) -> Document:
    pieces: list[str] = []
    spans: list[GoldSpan] = []
    position = cursor = 0
    for match in _SLOT.finditer(template):
        literal = template[cursor : match.start()]
        generator, gold_type = SLOTS[match.group(1)]
        value = generator(rng)
        position += len(literal)
        if gold_type is not None:
            spans.append(GoldSpan(position, position + len(value), gold_type))
        position += len(value)
        pieces += [literal, value]
        cursor = match.end()
    pieces.append(template[cursor:])
    return Document("".join(pieces), tuple(spans))


def build_corpus(
    *, held_out: bool = False, seed: int = 20261115, documents_per_template: int = 20
) -> list[Document]:
    rng = random.Random(seed + held_out)  # noqa: S311  (reproducibility, not security)
    templates = HELD_OUT_TEMPLATES if held_out else TEMPLATES
    return [render(template, rng) for template in templates for _ in range(documents_per_template)]
