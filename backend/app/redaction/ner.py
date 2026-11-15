"""Statistical detector for names and places, where no fixed format exists."""

import re
import threading
from collections.abc import Iterator
from functools import lru_cache

import spacy
import structlog
from spacy.language import Language

from app.redaction.types import PiiType, Span

log = structlog.get_logger()

MODEL_NAME = "de_core_news_sm"
# The model sees at most this many characters at once, so peak memory does not
# grow with the size of the prompt.
CHUNK_CHARACTERS = 4000

# Organisations and "MISC" are not personal data and are noisy in the small model.
_LABELS = {"PER": (PiiType.PERSON, 50), "LOC": (PiiType.LOCATION, 40)}


# The model often attaches a salutation, title or possessive to the name
# ("Frau Koch", "Dr. Weber", "Mein Mann Jonas Klein"). Those words are not PII.
_NOISE_WORD = (
    r"(?:Herrn|Herr|Frau|Dr\.|Prof\.|Meine[mnrs]?|Mein|Unsere[mnrs]?|Unser|Mann|Kundin|Kunde)"
)
_LEADING_NOISE = re.compile(rf"(?:{_NOISE_WORD}\s+)+")
_ONLY_NOISE = re.compile(_NOISE_WORD)
# Prompts to a language model start with an instruction: "Schreibe eine Absage …".
# The small model has no part-of-speech tagger loaded (ADR 0002) and takes many of
# these capitalised verbs for a place or a person: 22 of 55 in a probe. A word list
# is crude but safe: it only applies to the first word of a sentence, and none of
# these words is a plausible name or town. Verbs not listed here are still mis-tagged.
_INSTRUCTION_VERBS = frozenset(
    {
        "aktualisiere",
        "analysiere",
        "antworte",
        "beantworte",
        "berechne",
        "beschreibe",
        "bestätige",
        "bewerte",
        "bitte",
        "buche",
        "entscheide",
        "entwirf",
        "ergänze",
        "erinnere",
        "erkläre",
        "erstelle",
        "erzeuge",
        "extrahiere",
        "fasse",
        "finde",
        "formuliere",
        "generiere",
        "gib",
        "hilf",
        "informiere",
        "kontaktiere",
        "korrigiere",
        "kürze",
        "leite",
        "lies",
        "liste",
        "lösche",
        "mach",
        "mache",
        "melde",
        "nenne",
        "notiere",
        "ordne",
        "plane",
        "prüfe",
        "rufe",
        "sag",
        "sage",
        "schicke",
        "schreib",
        "schreibe",
        "sende",
        "sortiere",
        "storniere",
        "suche",
        "teile",
        "überarbeite",
        "übersetze",
        "verfasse",
        "vergleiche",
        "vervollständige",
        "zeige",
    }
)
_FIRST_WORD = re.compile(r"(\w+)\s*")
_SENTENCE_END = ".!?:\n"
# "Kontakt: …", "Bestellnummer 4711": a single capitalised noun used as a field label.
# Limited to one-word entities: dropping "Lena Krüger:" would leak a real name.
_LABEL_CONTEXT = re.compile(r"\s*(?::|\d)")


@lru_cache(maxsize=1)
def load_model() -> Language:
    """Loaded once per process, stripped down to what NER needs (see ADR 0002)."""
    nlp = spacy.load(
        MODEL_NAME,
        exclude=["tok2vec", "tagger", "morphologizer", "parser", "lemmatizer", "attribute_ruler"],
    )
    _prune_string_store(nlp)
    return nlp


def _prune_string_store(nlp: Language) -> None:
    """Drop the ~460k training-vocabulary strings the pipeline ships with.

    They cost about 90 MB of resident memory and NER never reads them: its
    embeddings hash the token attributes. Only strings behind cached lexemes
    (tokenizer special cases) and the entity labels are kept.
    """
    keep = {lexeme.text for lexeme in nlp.vocab} | set(nlp.get_pipe("ner").labels) | {""}  # type: ignore[attr-defined]
    try:
        nlp.vocab.strings._reset_and_load(keep)
    except AttributeError:  # pragma: no cover  (private spaCy API; pinned to 3.8)
        log.warning("ner_string_store_not_pruned")


def chunks(text: str, limit: int = CHUNK_CHARACTERS) -> Iterator[tuple[int, str]]:
    """Yield `(offset, piece)` covering the text, cut at a line, sentence or word boundary."""
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            for separator in ("\n", ". ", " "):
                cut = text.rfind(separator, start + limit // 2, end)
                if cut != -1:
                    end = cut + len(separator)
                    break
        yield start, text[start:end]
        start = end


def _starts_sentence(text: str, position: int) -> bool:
    before = text[:position].rstrip(" \t")
    return not before or before[-1] in _SENTENCE_END


class NerDetector:
    name = "spacy_ner"

    def __init__(self, nlp: Language) -> None:
        self._nlp = nlp
        # One inference at a time: concurrent calls multiply peak memory, and the
        # memory zone below is not safe to share between threads.
        self._lock = threading.Lock()

    def detect(self, text: str) -> list[Span]:
        spans: list[Span] = []
        for offset, piece in chunks(text):
            # The zone frees every string and lexeme created for this piece.
            # Without it the vocabulary grows with each new word ever seen.
            with self._lock, self._nlp.memory_zone():
                entities = [
                    (entity.start_char, entity.end_char, entity.label_)
                    for entity in self._nlp(piece).ents
                ]
            for start, end, label in entities:
                span = self._to_span(text, offset + start, offset + end, label)
                if span is not None:
                    spans.append(span)
        return spans

    def _to_span(self, text: str, start: int, end: int, label: str) -> Span | None:
        mapped = _LABELS.get(label)
        if mapped is None:
            return None
        pii_type, priority = mapped
        noise = _LEADING_NOISE.match(text, start, end)
        if noise is not None:
            start = noise.end()
        if _starts_sentence(text, start):
            first = _FIRST_WORD.match(text, start, end)
            if first is not None and first.group(1).lower() in _INSTRUCTION_VERBS:
                start = first.end()  # "Schreibe Petra Hoffmann" keeps the name
        value = text[start:end]
        # The small model also tags numbers, verbs and multi-line fragments.
        if (
            len(value.strip()) < 2
            or not value[0].isupper()
            or "\n" in value
            or any(char.isdigit() for char in value)
            or _ONLY_NOISE.fullmatch(value)
            or (" " not in value and _LABEL_CONTEXT.match(text, end))
        ):
            return None
        return Span(start, end, pii_type, self.name, priority)
