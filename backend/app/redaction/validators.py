"""Checksum validators. A match that fails its checksum is not treated as PII."""

from collections import Counter

# Registered IBAN lengths for the countries most likely to appear in German text.
IBAN_LENGTHS = {
    "DE": 22,
    "AT": 20,
    "CH": 21,
    "FR": 27,
    "NL": 18,
    "BE": 16,
    "LU": 20,
    "ES": 24,
    "IT": 27,
    "PL": 28,
}


def iban_is_valid(compact: str) -> bool:
    """ISO 13616: move the first four characters to the end, map A=10..Z=35, mod 97 == 1."""
    if not 15 <= len(compact) <= 34 or not compact.isalnum() or not compact.isascii():
        return False
    expected_length = IBAN_LENGTHS.get(compact[:2])
    if expected_length is not None and len(compact) != expected_length:
        return False
    rearranged = compact[4:] + compact[:4]
    return int("".join(str(int(char, 36)) for char in rearranged)) % 97 == 1


def steuer_id_check_digit(first_ten: str) -> int:
    """ISO 7064 MOD 11,10, as specified for the Steuerliche Identifikationsnummer."""
    product = 10
    for char in first_ten:
        total = (int(char) + product) % 10 or 10
        product = (total * 2) % 11
    return (11 - product) % 10


def steuer_id_is_valid(digits: str) -> bool:
    """11 digits, no leading zero, exactly one digit repeated (twice or three times)."""
    if len(digits) != 11 or not digits.isdecimal() or digits[0] == "0":
        return False
    repeats = sorted(count for count in Counter(digits[:10]).values() if count > 1)
    if repeats not in ([2], [3]):
        return False
    return steuer_id_check_digit(digits[:10]) == int(digits[10])
