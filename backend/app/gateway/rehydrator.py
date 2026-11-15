import re

from app.redaction.engine import PlaceholderVault

# A tail that could still grow into a placeholder: "<", "<PERS", "<PERSON_", "<PERSON_1".
_PARTIAL = re.compile(r"<[A-Z_]*\d*\Z")
# Longest real placeholder is "<DATE_OF_BIRTH_" plus a counter; anything longer is not one.
MAX_PLACEHOLDER_LENGTH = 32


class StreamRehydrator:
    """Re-hydrate a response that arrives in arbitrary pieces.

    Upstream chunk boundaries can fall inside a placeholder ("<PERS" + "ON_1>").
    Replacing chunk by chunk would let half a placeholder through to the client.
    So text is released only up to the last position that cannot be the start
    of a placeholder; the rest waits for the next chunk.

    Invariant (tested for every split position): the concatenated output equals
    `vault.rehydrate(whole_text)`.
    """

    def __init__(self, vault: PlaceholderVault) -> None:
        self._vault = vault
        self._pending = ""

    def feed(self, chunk: str) -> str:
        self._pending += chunk
        partial = _PARTIAL.search(self._pending)
        if partial is not None and len(self._pending) - partial.start() <= MAX_PLACEHOLDER_LENGTH:
            cut = partial.start()
        else:
            cut = len(self._pending)
        ready, self._pending = self._pending[:cut], self._pending[cut:]
        return self._vault.rehydrate(ready)

    def flush(self) -> str:
        """Call once after the last chunk: releases whatever was held back."""
        ready, self._pending = self._pending, ""
        return self._vault.rehydrate(ready)
