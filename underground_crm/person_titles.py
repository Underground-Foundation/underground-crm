"""
Cleaning and validation for ``Person.prefix`` — a name title / honorific such
as "Mr", "Mrs", "Dr", "Prof".

What a name title is
--------------------
An honorific form of address that begins a name. It is not a job title, an
academic rank, or free text a person typed into the wrong box. Legacy imports
carry plenty of the latter — "Citizen", stray given names — and this module is
where they are caught rather than stored.

Multi-word titles
-----------------
A prefix can legitimately be a *sequence* of titles — "Hon Dr", "Rev Dr". The
AIHW METEOR data element this mirrors, "Person (name)—name title", is
explicitly repeatable and gives "Hon Dr" as its worked example (guide for use,
"Name title functions as a prefix to a person's name and should not be confused
with job titles"):

    https://meteor.aihw.gov.au/content/453731

So a prefix is treated here as whitespace-separated title words, each of which
must independently be a recognised title; the cleaned result rejoins them with
a single space.

Recogniser
----------
We do not keep our own list of honorifics. Membership is tested against
python-nameparser's title set (``nameparser.config.CONSTANTS.titles``), which
is large and community-maintained. That set is deliberately permissive — it
exists to strip leading role words while parsing names, so "professor" and
"associate" are members too and "Associate Professor" is accepted as a title.
What it still rejects is genuine non-titles ("Citizen") and anything a person
fat-fingered into the field.

Matching is case- and punctuation-insensitive ("mrs.", "MRS", "Mrs" all match);
the stored form is the recognised spelling, capitalised ("HON DR" -> "Hon Dr").
"""

from __future__ import annotations

import re

from nameparser.config import CONSTANTS

__all__ = ["InvalidNamePrefixError", "clean_name_prefix"]

# Punctuation a title word may be padded with in legacy data: "Mrs.", "Dr,".
# Hyphens are deliberately kept — "commander-in-chief" is a single nameparser
# title token, not three words.
_STRIP_CHARS = ".,;:()[]{}'\" "

_WHITESPACE_RE = re.compile(r"\s+")


class InvalidNamePrefixError(ValueError):
    """A non-empty prefix that is not one or more recognised name titles.

    ``bad_words`` holds the whitespace-separated tokens of ``raw`` that are not
    recognised titles. It is empty when the prefix is rejected only for
    exceeding the ``max_length`` of the field it is bound for.

    Subclasses ``ValueError`` to match ``InvalidPhoneNumberError`` in
    ``underground_crm.contactability``: a bad value on an import row or a form
    is data to be handled, not a programming error.
    """

    def __init__(
        self,
        raw: str,
        bad_words: list[str] | None = None,
        reason: str | None = None,
    ):
        self.raw = raw
        self.bad_words = bad_words or []
        if reason is not None:
            detail = reason
        elif self.bad_words:
            joined = ", ".join(repr(word) for word in self.bad_words)
            detail = f"contains {joined}, which is not a recognised name title"
        else:
            detail = "is not a recognised name title"
        super().__init__(f"{raw!r} {detail}")


def clean_name_prefix(raw: str | None, *, max_length: int | None = None) -> str | None:
    """Return ``raw`` normalised to canonical name-title form, or ``None``.

    ``None`` or a blank / whitespace-only value returns ``None``: a person need
    not have a prefix.

    Any other value is split on whitespace and every word must be a recognised
    title (see the module docstring for the recogniser). A value that is not
    raises :class:`InvalidNamePrefixError` rather than being silently dropped —
    the caller decides whether that is warn-and-skip (the CSV importer) or a
    hard error (``Person.save``).

    When ``max_length`` is given, a cleaned result longer than it also raises
    :class:`InvalidNamePrefixError`: nameparser accepts words that do not fit
    ``Person.prefix`` ("Associate Professor" normalises to 19 characters).
    """
    if raw is None:
        return None
    collapsed = _WHITESPACE_RE.sub(" ", raw).strip()
    if not collapsed:
        return None

    cleaned_words: list[str] = []
    bad_words: list[str] = []
    for word in collapsed.split(" "):
        key = word.strip(_STRIP_CHARS).lower()
        if key and key in CONSTANTS.titles:
            cleaned_words.append(key.capitalize())
        else:
            bad_words.append(word)

    if bad_words:
        raise InvalidNamePrefixError(raw, bad_words=bad_words)

    cleaned = " ".join(cleaned_words)
    if max_length is not None and len(cleaned) > max_length:
        raise InvalidNamePrefixError(
            raw,
            reason=(
                f"normalises to {cleaned!r} ({len(cleaned)} characters), which does not "
                f"fit the {max_length}-character prefix field"
            ),
        )
    return cleaned
