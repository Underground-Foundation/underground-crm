"""
Custom StreamField blocks with built-in sanitization.

The standard Wagtail ``RawHTMLBlock`` stores and renders arbitrary HTML
verbatim, which creates a stored-XSS vector if an authorised editor account
is compromised.  ``SanitizedRawHTMLBlock`` overrides ``clean()`` to run
every value through *nh3* before it reaches the database, enforcing a strict
allow-list of tags, attributes, and URL schemes.
"""

from __future__ import annotations

import nh3
from wagtail.blocks import RawHTMLBlock

from underground_crm.sanitization import (
    ALLOWED_ATTRIBUTES,
    ALLOWED_TAGS,
    ALLOWED_URL_SCHEMES,
)


class SanitizedRawHTMLBlock(RawHTMLBlock):
    """A ``RawHTMLBlock`` that sanitizes its value on save.

    All HTML is passed through ``nh3.clean()`` with the allow-lists defined
    in :mod:`underground_crm.sanitization`.  Dangerous elements
    (``<script>``, ``<iframe>``, ``<object>``, ``<embed>``, ``<form>``,
    event-handler attributes, ``javascript:`` URIs, etc.) are stripped
    before the value ever reaches the database.
    """

    def clean(self, value: str) -> str:
        value = super().clean(value)
        return nh3.clean(
            value,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            url_schemes=ALLOWED_URL_SCHEMES,
            # Disable nh3's automatic ``rel`` injection so editors can set
            # their own ``rel`` values (e.g. ``nofollow``, ``noopener``).
            # Without this, nh3 raises a ValueError when ``rel`` appears in
            # the per-tag attributes allow-list.
            link_rel=None,
        )
