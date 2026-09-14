"""
Conservative correction of an Address's street name and suburb spelling from
its Addressr match.

Geocoding succeeds on addresses typed with small mistakes ("19 carnavon St,
brunswik"), because Addressr's search is fuzzy. When the match plainly
describes the same property, its spelling of the street name and suburb is
adopted. Anything that could mean Addressr matched a different property
rather than the same one misspelled leaves the record untouched: a
different street number, postcode, street type or street suffix, a
different number of words, or a word too unlike its counterpart.

Only the street name and the suburb are ever rewritten. The street number,
any unit or level prefix, and the street type and suffix are kept exactly as
submitted, apart from capitalizing words that were submitted in lower case.
"""

import re
from difflib import SequenceMatcher

from underground_crm.addressr import GnafCode, StructuredAddress
from underground_crm.models.address import Address

# How alike two spellings of one word must be, as a SequenceMatcher ratio
# (0 = nothing in common, 1 = identical), for one to count as a misspelling of
# the other. At 0.8, "Carnavon" (0.94 against "Carnarvon") and "Cok" (0.86
# against "Cook") are corrected, whereas "Hay" against "Kay" (0.67) is not,
# since those are more likely two different streets than a typing slip.
MINIMUM_SPELLING_SIMILARITY: float = 0.8

# A submitted line1 split into the street number and the street after it,
# e.g. "Unit 2, 19 Carnarvon St" -> ("Unit 2, ", "19", " ", "Carnarvon St").
# The number may carry a letter suffix or be a range ("551A", "480-490"), and
# the street may contain no digits, so the number found is always the one
# directly before the street name rather than a unit or level number.
_STREET_LINE_RE = re.compile(
    r"^(?P<prefix>(?:.*[\s,/])?)"
    r"(?P<number>\d+[A-Za-z]?(?:\s*-\s*\d+[A-Za-z]?)?)"
    r"(?P<separator>\s+)"
    r"(?P<street>[^\d\s][^\d]*?)\s*$"
)


def spelling_similarity(first: str, second: str) -> float:
    """How alike two words are, ignoring case: 1.0 for identical spellings,
    falling towards 0.0 as they share fewer letters in order."""
    return SequenceMatcher(None, first.casefold(), second.casefold()).ratio()


def _comparable(word: str) -> str:
    """A word reduced for comparison, so that "St." agrees with "ST"."""
    return word.rstrip(".").casefold()


def _capitalized_if_lower_case(word: str) -> str:
    """The word with its first letter capitalized if it was submitted
    entirely in lower case, and otherwise unchanged, so that deliberate
    capitalization such as "McDonald" is never flattened into "Mcdonald"."""
    return word.title() if word.islower() else word


def _restyle(replacement: str, original: str) -> str:
    """The replacement word in the capitalization style of the word it
    replaces: all capitals if the original was submitted in capitals,
    otherwise title case (Addressr's own spelling is always in capitals)."""
    return replacement.upper() if original.isupper() else replacement.title()


def _corrected_name(submitted: str, canonical: str) -> str | None:
    """
    A submitted street name or suburb with each misspelled word replaced by
    Addressr's spelling, or None when the submitted name is not a minor
    misspelling of the canonical one at all: a different number of words, or
    any word too unlike its counterpart.
    """
    submitted_words = submitted.split()
    canonical_words = canonical.split()
    if not submitted_words or len(submitted_words) != len(canonical_words):
        return None

    corrected: list[str] = []
    for submitted_word, canonical_word in zip(submitted_words, canonical_words):
        if _comparable(submitted_word) == _comparable(canonical_word):
            corrected.append(_capitalized_if_lower_case(submitted_word))
        elif (
            spelling_similarity(_comparable(submitted_word), canonical_word)
            >= MINIMUM_SPELLING_SIMILARITY
        ):
            corrected.append(_restyle(canonical_word, submitted_word))
        else:
            return None
    return " ".join(corrected)


def _is_either_gnaf_form(word: str, gnaf_code: GnafCode) -> bool:
    """True if the word is the code or the name of a G-NAF street type or
    suffix, e.g. "Street" or "St" for GnafCode(code="STREET", name="ST")."""
    return _comparable(word) in {_comparable(gnaf_code.code), _comparable(gnaf_code.name)}


def _corrected_street(submitted_street: str, matched: StructuredAddress) -> str | None:
    """
    The submitted street (everything after the street number, e.g. "carnavon
    Street") with its name corrected, or None when it is not the matched
    street.

    The submitted words are read in G-NAF's order: first as many words as the
    matched street name has, then the street type if the matched street has
    one, then its suffix if it has one. The type and suffix words must each be
    one of their two G-NAF forms, because "Carnarvon Rd" and "Carnarvon St"
    can be two different streets in the same suburb, but they are otherwise
    kept as submitted rather than corrected.
    """
    if not matched.street_name:
        return None
    submitted_words = submitted_street.split()
    name_word_count = len(matched.street_name.split())
    trailing_codes = [code for code in (matched.street_type, matched.street_suffix) if code]
    if len(submitted_words) != name_word_count + len(trailing_codes):
        return None

    name = _corrected_name(" ".join(submitted_words[:name_word_count]), matched.street_name)
    if name is None:
        return None

    trailing_words = submitted_words[name_word_count:]
    for word, gnaf_code in zip(trailing_words, trailing_codes):
        if not _is_either_gnaf_form(word, gnaf_code):
            return None
    return " ".join([name, *(_capitalized_if_lower_case(word) for word in trailing_words)])


def _normalized_number(number: str) -> str:
    return re.sub(r"\s+", "", number).casefold()


def correct_minor_misspellings(submitted_address: Address, matched: StructuredAddress) -> list[str]:
    """
    Adopt the matched address's spelling of the street name and suburb when
    the submitted address is the same property with those names slightly
    misspelled or miscapitalized, and return the names of the fields changed
    ("line1", "city"). The address is modified in place but not saved.

    Nothing at all is changed unless every check agrees that the match is the
    same property: the street numbers must be identical, the postcodes must be
    identical when both are known, the street type and suffix must agree, and
    both the street name and the suburb must be at most minor misspellings of
    the match. A match failing any check may be a neighboring property that
    Addressr's fuzzy search preferred, so its spelling cannot be trusted as a
    correction.
    """
    if not submitted_address.line1 or not matched.street_number:
        return []
    submitted_line = _STREET_LINE_RE.match(submitted_address.line1)
    if submitted_line is None:
        return []

    if _normalized_number(submitted_line.group("number")) != _normalized_number(
        matched.street_number
    ):
        # Different addresses
        return []
    if (
        submitted_address.postcode
        and matched.postcode
        and submitted_address.postcode.strip() != matched.postcode.strip()
    ):
        # Different addresses
        return []

    street = _corrected_street(submitted_line.group("street"), matched)
    if street is None:
        return []
    line1 = (
        submitted_line.group("prefix")
        + submitted_line.group("number")
        + submitted_line.group("separator")
        + street
    )

    city = submitted_address.city
    if submitted_address.city and matched.city:
        city = _corrected_name(submitted_address.city, matched.city)
        if city is None:
            return []

    changed: list[str] = []
    for field, value in (("line1", line1), ("city", city)):
        if value != getattr(submitted_address, field):
            setattr(submitted_address, field, value)
            changed.append(field)
    return changed
