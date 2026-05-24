"""
Allow-lists for HTML sanitization.

These constants define which tags, attributes, and URL schemes are
permitted by ``SanitizedRawHTMLBlock``.  They are kept in a separate
module so that tests can import them without triggering Django or
Wagtail initialisation.
"""

from __future__ import annotations

ALLOWED_TAGS: set[str] = {
    # Structure and layout
    "div", "span", "p", "br", "hr",
    "section", "article", "aside", "header", "footer", "nav", "main",
    # Headings
    "h1", "h2", "h3", "h4", "h5", "h6",
    # Links and media
    "a", "img",
    "video", "audio", "source", "picture",
    # Lists
    "ul", "ol", "li",
    "dl", "dt", "dd",
    # Tables
    "table", "thead", "tbody", "tfoot", "tr", "th", "td",
    # Inline formatting
    "strong", "em", "b", "i", "u", "s", "sub", "sup", "small", "mark",
    # Semantic / miscellaneous
    "blockquote", "pre", "code", "figure", "figcaption",
    "details", "summary",
    "abbr", "time", "cite", "address",
}

# Attributes that are safe on *any* tag.
_GLOBAL_ATTRS: set[str] = {"class", "id", "style", "role", "title", "lang", "dir"}

ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "*": _GLOBAL_ATTRS,
    "a": {"href", "target", "rel"},
    "img": {"src", "alt", "width", "height", "loading"},
    "video": {"src", "controls", "autoplay", "loop", "muted", "preload", "poster",
              "width", "height"},
    "audio": {"src", "controls", "autoplay", "loop", "muted", "preload"},
    "source": {"src", "type", "media"},
    "td": {"colspan", "rowspan", "scope"},
    "th": {"colspan", "rowspan", "scope"},
    "time": {"datetime"},
    "abbr": {"title"},
}

# Only allow safe URL schemes — blocks javascript: URIs.
ALLOWED_URL_SCHEMES: set[str] = {"http", "https", "mailto"}
