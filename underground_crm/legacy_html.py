"""
Turning legacy CMS page markup into Wagtail StreamField blocks.

The import_pages management command drops a page's whole ``id="content"``
element into a single Raw HTML block. That is a faithful archive but hardcodes
the styling from the time of import, which may include artifacts created due to
an import from Google Docs.

Here we therefore deconstruct the content into Wagtail blocks, falling back to
a Raw HTML block for the parts it cannot recreate. The fallback is per-tag,
not for the whole page.

Nothing here imports Django or Wagtail. Images are the one part that needs
the database, and they are reached through an injected ``image_resolver``
callable (see underground_crm/legacy_images.py for the real one), so the
parsing can be exercised in tests without a settings module.
"""

import copy
import json
import re
from typing import Callable, List, NamedTuple, Optional, Tuple

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

from underground_crm.image_alignment import DEFAULT_IMAGE_ALIGNMENT, ImageAlignment

# Block names as registered in underground_crm.models.pages.BASIC_PAGE_BLOCKS.
RICH_TEXT_BLOCK = "rich_text"
RAW_HTML_BLOCK = "html"
IMAGE_BLOCK = "image"
BUTTON_BLOCK = "button"

# The Image block's alignment for each CSS ``float`` value a legacy image may
# carry. Any other value (or none) is judged on the image's width instead.
_FLOAT_ALIGNMENTS = {"left": ImageAlignment.LEFT, "right": ImageAlignment.RIGHT}

# Given (src, alt), return the primary key of a Wagtail image, or None when
# the image cannot be brought across — in which case the <img> is preserved
# verbatim in a Raw HTML block instead.
ImageResolver = Callable[[str, str], Optional[object]]

# The tags a RichTextBlock built from BASIC_PAGE_BLOCKS' feature list can
# actually hold: features are h2/h3/h4, bold, italic, underline, link, ol,
# ul, hr, blockquote and image. Anything outside this set would be discarded
# the first time an editor opened the page in Draftail, so a node containing
# one is sent to a Raw HTML block instead, where it survives intact.
RICH_TEXT_BLOCK_TAGS = frozenset({"p", "h2", "h3", "h4", "ul", "ol", "li", "hr", "blockquote"})
RICH_TEXT_INLINE_TAGS = frozenset({"a", "b", "strong", "i", "em", "u", "br"})

# Legacy bodies carry their own <h1> (the headline is also the page title) and
# occasionally an <h5>/<h6>. Draftail offers h2–h4 only, so they are moved to
# the nearest available level rather than disqualifying the paragraph.
HEADING_DEMOTIONS = {"h1": "h2", "h5": "h4", "h6": "h4"}

# Wrappers that carry no meaning of their own: the walk descends through them
# and treats their children as though they had been written at the top level.
TRANSPARENT_CONTAINER_TAGS = frozenset({"div", "section", "article", "main", "center"})

# Classes that only position a transparent wrapper — Bootstrap's grid and
# spacing utilities, plus the handful of legacy CMS layout hooks. A
# wrapper whose classes are all in here (or that has no classes at all) is
# descended through; one with a class we do not recognise is left alone and
# preserved as raw HTML, because the class may well be doing something.
_LAYOUT_CLASS_PATTERN = re.compile(
    r"""^(
        (m|p)[tbeslrxy]?-(auto|\d+)   # mb-4, pt-2, mx-auto, ...
        | row | col | col-\d+ | col-(sm|md|lg|xl|xxl)-\d+
        | container | container-fluid | clearfix
        | w-\d+ | h-\d+
        | text-(start|end|left|center|right)
        | d-(none|block|flex|inline|inline-block)
        | d-(sm|md|lg|xl|xxl)-(none|block|flex|inline|inline-block)
        | border | border-\w+
        | intro | content | body
    )$""",
    re.VERBOSE,
)

# Elements a legacy CMS might add inside the content element that are not
# part of the article itself. These tags are matched
# by ID or CSS class.
_EXTRANEOUS_IDENTIFIERS = frozenset(
    {
        "headline",
        "toc",
        "byline",
        "comments",
        "comment-form",
        "disqus_thread",
        "nb-comments",
        "pagination",
        "blog-post-navigation",
        "social-share",
        "nb-social-share",
        "share-buttons",
        "like-and-share",
    }
)
_EXTRANEOUS_TAGS = frozenset({"script", "style", "noscript", "nav", "header", "footer"})

# CSS properties that never survive into a Wagtail block and never meant
# anything in the first place: the font and spacing noise that Google Docs
# and the legacy CMS editor paste around every run of text.
_DISCARDABLE_PROPERTIES = frozenset(
    {
        "font-family",
        "font-size",
        "font-variant",
        "font-stretch",
        "font-kerning",
        "line-height",
        "letter-spacing",
        "word-spacing",
        "text-indent",
        "orphans",
        "widows",
        "-webkit-text-stroke-width",
        "-webkit-font-smoothing",
    }
)

# Properties that are noise only at certain values: font-weight: 400 is the
# Google Docs artifact, font-weight: 700 is a genuine bold.
_DISCARDABLE_VALUES = {
    "font-weight": frozenset({"400", "normal", "inherit", "initial", "unset"}),
    "font-style": frozenset({"normal", "inherit", "initial", "unset"}),
    "text-decoration": frozenset({"none", "inherit", "initial", "unset"}),
    "text-decoration-line": frozenset({"none"}),
    "vertical-align": frozenset({"baseline"}),
    "white-space": frozenset({"normal", "pre-wrap"}),
}

_BOLD_WEIGHTS = frozenset({"bold", "bolder", "600", "700", "800", "900"})
_ITALIC_STYLES = frozenset({"italic", "oblique"})
_UNDERLINE_PROPERTIES = frozenset({"text-decoration", "text-decoration-line"})
_UNDERLINE_VALUES = frozenset({"underline"})

# Attributes a <span> may carry and still be treated as a pure wrapper. class
# and id are included because neither can be represented in a Wagtail block:
# the legacy stylesheet is not coming with us, so a class here is already
# inert — keeping the span would only push its paragraph into raw HTML for
# nothing.
_UNWRAPPABLE_SPAN_ATTRIBUTES = frozenset({"style", "class", "id", "lang", "dir"})

# Inline wrappers that are always redundant once their styling is gone.
_UNWRAPPABLE_TAGS = frozenset({"span", "font"})

_STYLE_DECLARATION = re.compile(r"([-a-zA-Z]+)\s*:\s*([^;]+)")
_PERCENTAGE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%\s*$")

# Only used to mint new tags; bs4 requires a soup to build one from.
_TAG_FACTORY = BeautifulSoup("", "html.parser")


def _new_tag(name: str) -> Tag:
    return _TAG_FACTORY.new_tag(name)


def _is_blank(node) -> bool:
    """True for whitespace-only text (including the &nbsp; legacy editors leave behind)."""
    if isinstance(node, Comment):
        return True
    if isinstance(node, NavigableString):
        return not str(node).replace("\xa0", " ").strip()
    return False


def _meaningful_children(tag: Tag) -> List:
    return [child for child in tag.contents if not _is_blank(child)]


def _classes(tag: Tag) -> List[str]:
    classes = tag.get("class") or []
    return classes if isinstance(classes, list) else [classes]


def parse_style(value: str) -> List[Tuple[str, str]]:
    """Split a style attribute into (property, value) pairs, lowercased and trimmed."""
    if not value:
        return []
    return [
        (prop.strip().lower(), val.strip().lower())
        for prop, val in _STYLE_DECLARATION.findall(value)
    ]


def format_style(declarations: List[Tuple[str, str]]) -> str:
    return "; ".join(f"{prop}: {value}" for prop, value in declarations)


def _reduce_style(
    declarations: List[Tuple[str, str]],
) -> Tuple[List[Tuple[str, str]], bool, bool, bool]:
    """
    Drop the presentational noise from a style attribute.

    Returns the declarations worth keeping, plus whether the discarded ones
    asked for bold, italic and underline — those three are not noise, they
    are markup the legacy editor chose to express as CSS, so the caller
    re-expresses them as <strong>/<em>/<u> rather than losing them.
    """
    kept: List[Tuple[str, str]] = []
    is_bold = False
    is_italic = False
    is_underline = False
    for prop, value in declarations:
        if prop == "font-weight" and value in _BOLD_WEIGHTS:
            is_bold = True
            continue
        if prop == "font-style" and value in _ITALIC_STYLES:
            is_italic = True
            continue
        if prop in _UNDERLINE_PROPERTIES and value in _UNDERLINE_VALUES:
            is_underline = True
            continue
        if prop in _DISCARDABLE_PROPERTIES:
            continue
        if value in _DISCARDABLE_VALUES.get(prop, frozenset()):
            continue
        kept.append((prop, value))
    return kept, is_bold, is_italic, is_underline


def _wrap_contents(tag: Tag, name: str) -> None:
    """Move everything inside `tag` into a new child element of the given name."""
    wrapper = _new_tag(name)
    for child in list(tag.contents):
        wrapper.append(child.extract())
    tag.append(wrapper)


def strip_presentational_markup(root: Tag) -> None:
    """
    Remove the styling artifacts legacy editors leave behind, in place.

    The headline case is Google Docs' (and the legacy CMS's)
    ``<span style="font-weight: 400;">``, which wraps a run of perfectly
    ordinary text in a span that asks for the weight it already had. Once the
    declaration is dropped the span has nothing left to say, so it is
    unwrapped and its children become direct children of its own parent.
    ``text-decoration: underline`` is the same story — genuine markup
    expressed as CSS — and is re-expressed as ``<u>``, a registered Draftail
    feature, rather than dropped as noise.

    A span that still carries something meaningful afterwards — a color, a
    background — is left standing. That costs its paragraph a rich text
    block and wins it a raw HTML one, which is the right trade: the styling
    is preserved rather than silently dropped.

    <img> is skipped entirely: nothing from its style attribute reaches a
    Wagtail block, and _image_block() still needs to read the width and float
    out of it to choose an alignment.
    """
    changed = True
    # Unwrapping a span can expose another span that was previously nested,
    # so the pass repeats until the tree stops moving. Google Docs routinely
    # nests three deep.
    while changed:
        changed = False
        for tag in list(root.find_all(True)):
            if tag.name == "img" or tag.decomposed:
                continue
            declarations = parse_style(tag.get("style", ""))
            kept, is_bold, is_italic, is_underline = _reduce_style(declarations)
            if kept != declarations:
                changed = True
                if kept:
                    tag["style"] = format_style(kept)
                else:
                    del tag["style"]
            if tag.name not in _UNWRAPPABLE_TAGS:
                # A block tag keeps its identity; only the noise went away.
                # Bold/italic/underline expressed as CSS on a <p> applies to
                # the whole paragraph, so re-express it inside the paragraph.
                if is_bold:
                    _wrap_contents(tag, "strong")
                if is_italic:
                    _wrap_contents(tag, "em")
                if is_underline:
                    _wrap_contents(tag, "u")
                continue
            if kept or set(tag.attrs) - _UNWRAPPABLE_SPAN_ATTRIBUTES:
                continue
            if is_bold:
                _wrap_contents(tag, "strong")
            if is_italic:
                _wrap_contents(tag, "em")
            if is_underline:
                _wrap_contents(tag, "u")
            tag.unwrap()
            changed = True


def get_body_soup(document_or_content: Tag) -> Optional[Tag]:
    """
    Narrow a legacy page down to the element that holds its article.

    `document_or_content` is either the whole parsed page or the page's own
    ``id="content"`` element; both are accepted so that a caller which has
    already extracted that element need not extract it a second time. Returns
    None when the page has no ``id="content"`` region at all.

    The ``id="content"`` region also picks up the byline and table-of-contents
    elements alongside the article, so a tighter container is preferred when
    one exists. The legacy CMS's own ``div.content`` (wrapping just the
    article, inside a ``div#intro``) comes first, since it excludes those
    elements. Next best is the second ``id="content"`` div that the legacy CMS
    nests inside ``<main id="content">`` (import_pages' extract_page_size
    relies on the same nesting), although it can still hold them. Then comes
    any ``div.container``, and finally the ``id="content"`` region itself.
    """
    if document_or_content.get("id") == "content":
        content = document_or_content
    else:
        content = document_or_content.find(id="content")
    if content is None:
        return None
    return (
        content.find("div", class_="content")
        or content.find("div", id="content")
        or content.find("div", class_="container")
        or content
    )


def strip_extraneous_elements(container: Tag) -> None:
    """Remove the non-article elements that surround the article, in place."""
    for comment in list(container.find_all(string=lambda text: isinstance(text, Comment))):
        comment.extract()
    for tag in list(container.find_all(True)):
        if tag.decomposed:
            continue
        if tag.name in _EXTRANEOUS_TAGS:
            tag.decompose()
            continue
        identifiers = {identifier.lower() for identifier in _classes(tag)}
        element_id = tag.get("id")
        if element_id:
            identifiers.add(str(element_id).lower())
        if identifiers & _EXTRANEOUS_IDENTIFIERS:
            tag.decompose()


def _is_transparent_container(tag: Tag) -> bool:
    """True for a wrapper that only positions its children."""
    if tag.name not in TRANSPARENT_CONTAINER_TAGS:
        return False
    if set(tag.attrs) - {"class", "id", "style", "role", "align"}:
        return False
    if parse_style(tag.get("style", "")):
        return False
    return all(_LAYOUT_CLASS_PATTERN.match(name) for name in _classes(tag))


def _single_cell(tag: Tag) -> Optional[Tag]:
    """
    The one <td> of a single-cell layout table, or None.

    Legacy editors use a one-by-one table as a text frame. Its cell's
    children are the content, so the walk steps through it exactly as it
    steps through a <div>.
    """
    if tag.name != "table":
        return None
    cells = tag.find_all("td")
    if len(cells) != 1:
        return None
    rows = tag.find_all("tr")
    if len(rows) > 1:
        return None
    return cells[0]


def _hex_colour(value: str) -> str:
    value = value.strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value) else ""


def _button_width(tag: Tag, anchors: List[Tag]) -> str:
    """
    Map a legacy button's width onto the ButtonBlock width choices.

    Only the button's own surface — the anchor, or the colored table the
    editor draws it with — is measured. The wrapper around it is usually
    ``width: 100%`` merely to center a fixed-width button inside the column,
    and reading that would stretch a 300px button across the page.
    """
    for candidate in [*anchors, *tag.find_all("table")]:
        classes = {name.lower() for name in _classes(candidate)}
        if "w-100" in classes:
            return "w-100"
        if "w-50" in classes:
            return "w-50"
        declared = dict(parse_style(candidate.get("style", ""))).get("width", "")
        declared = declared or str(candidate.get("width", ""))
        match = _PERCENTAGE.match(declared)
        if not match:
            continue
        percentage = float(match.group(1))
        if percentage >= 90:
            return "w-100"
        return "w-50" if percentage >= 40 else ""
    return ""


def _as_button(tag: Tag) -> Optional[dict]:
    """
    Recognize a legacy call-to-action button and return its block value.

    The legacy CMS's editor renders one as a ``div.nb-tmce-btn`` wrapping a
    colored single-cell table with two anchors to the same destination: an
    empty one stretched across the cell to make the whole block clickable,
    and the visible one carrying the label. A theme button is plainer — an
    anchor with Bootstrap's ``btn`` class. Both reduce to the same four
    fields.

    Returns None when the element is not a button, in which case the caller
    carries on classifying it normally.
    """
    anchors = tag.find_all("a") if tag.name != "a" else [tag]
    if not anchors:
        return None
    destinations = {str(anchor.get("href", "")) for anchor in anchors}
    if len(destinations) != 1:
        return None
    url = destinations.pop()
    if not url:
        return None
    labels = [anchor.get_text(strip=True) for anchor in anchors]
    label = next((text for text in labels if text), "")
    if not label:
        return None
    # Anything outside the anchors means this is prose that happens to
    # contain a link, not a button.
    if tag.get_text(strip=True) != "".join(labels).strip():
        return None
    classes = {name.lower() for name in _classes(tag)}
    is_editor_button = bool(classes & {"nb-btn", "nb-tmce-btn"})
    is_themed_anchor = any("btn" in {name.lower() for name in _classes(a)} for a in anchors)
    if not (is_editor_button or is_themed_anchor or _single_cell(tag) is not None):
        return None
    background = ""
    for candidate in [tag, *tag.find_all(True)]:
        declarations = dict(parse_style(candidate.get("style", "")))
        background = _hex_colour(declarations.get("background-color", ""))
        if background:
            break
    return {
        "type": BUTTON_BLOCK,
        "value": {
            "text": label,
            "url": url,
            "background_color": background,
            "width": _button_width(tag, anchors),
        },
    }


def _image_alignment(img: Tag) -> str:
    """
    Choose an Image block alignment from however the legacy markup sized the
    picture. A float maps straight onto a left or right alignment, and
    anything else is judged on width, since that is what the original authors
    reached for (``width="60%"``).
    """
    declarations = dict(parse_style(img.get("style", "")))
    float_alignment = _FLOAT_ALIGNMENTS.get(declarations.get("float", ""))
    if float_alignment:
        return float_alignment
    declared = declarations.get("width", "") or str(img.get("width", ""))
    match = _PERCENTAGE.match(declared)
    if match and float(match.group(1)) < 90:
        return ImageAlignment.HALF_WIDTH
    return DEFAULT_IMAGE_ALIGNMENT


def _image_block(img: Tag, image_resolver: Optional[ImageResolver]) -> Optional[dict]:
    """An Image block for this <img>, or None when it cannot be brought across."""
    if image_resolver is None:
        return None
    source = (img.get("src") or "").strip()
    if not source:
        return None
    image_id = image_resolver(source, (img.get("alt") or "").strip())
    if image_id is None:
        return None
    return {
        "type": IMAGE_BLOCK,
        "value": {
            "image": image_id,
            "caption": (img.get("title") or "").strip(),
            "alignment": _image_alignment(img),
        },
    }


def _rich_text_name(tag: Tag) -> Optional[str]:
    """The tag name this element would take inside rich text, or None."""
    name = HEADING_DEMOTIONS.get(tag.name, tag.name)
    return name if name in RICH_TEXT_BLOCK_TAGS else None


def _is_rich_text_compatible(tag: Tag) -> bool:
    """True when the whole subtree can be expressed with the block's features."""
    if _rich_text_name(tag) is None:
        return False
    for descendant in tag.find_all(True):
        name = HEADING_DEMOTIONS.get(descendant.name, descendant.name)
        if name in RICH_TEXT_INLINE_TAGS:
            if name == "a" and not descendant.get("href"):
                return False
            continue
        if name not in RICH_TEXT_BLOCK_TAGS:
            return False
    return True


def _sanitize_as_rich_text(tag: Tag) -> Tag:
    """
    A copy of `tag` carrying only what Wagtail stores in a rich text value:
    the demoted tag names, and a href on each link. Classes, ids and any
    remaining style are dropped — a rich text value has nowhere to put them,
    so keeping them would only mean the editor silently discarding them the
    first time the page was opened.
    """
    clone = copy.copy(tag)
    for element in [clone, *clone.find_all(True)]:
        element.name = HEADING_DEMOTIONS.get(element.name, element.name)
        href = element.get("href") if element.name == "a" else None
        element.attrs = {"href": href} if href else {}
    return clone


def _has_visible_content(tag: Tag) -> bool:
    """False for the empty paragraphs scattered between blocks by the legacy editor."""
    if tag.name == "hr" or tag.find("hr"):
        return True
    return bool(tag.get_text().replace("\xa0", " ").strip())


def fragment_html(tag: Tag) -> str:
    """
    The markup of a node destined for a Raw HTML block, exactly as it was
    written.

    Deliberately not prettified, unlike the archive file import_pages writes:
    indenting an inline element inserts whitespace inside it, which is the
    difference between ``<code>/membership</code>`` and a code span padded
    with spaces. The point of the raw HTML fallback is that the part it
    carries is untouched.
    """
    return str(tag).strip()


def _paragraph_runs(tag: Tag) -> Optional[List]:
    """
    Split a paragraph that mixes prose with images into the runs it is really
    made of, preserving document order: text, picture, more text.

    Returns None when the images are not direct children — a linked or
    otherwise wrapped image cannot be lifted out without losing the wrapper,
    so the whole node goes to raw HTML instead.
    """
    images = tag.find_all("img")
    if not images:
        return []
    if any(img.parent is not tag for img in images):
        return None
    runs: List = []
    current: List = []
    for child in tag.contents:
        if isinstance(child, Tag) and child.name == "img":
            if current:
                runs.append(("nodes", current))
                current = []
            runs.append(("image", child))
        else:
            current.append(child)
    if current:
        runs.append(("nodes", current))
    return runs


class _BlockCollector:
    """Accumulates blocks, merging runs of rich text and of raw HTML as it goes."""

    def __init__(self):
        self.blocks: List[dict] = []

    def add_rich_text(self, html: str) -> None:
        if not html.strip():
            return
        if self.blocks and self.blocks[-1]["type"] == RICH_TEXT_BLOCK:
            self.blocks[-1]["value"] += html
            return
        self.blocks.append({"type": RICH_TEXT_BLOCK, "value": html})

    def add_raw_html(self, html: str) -> None:
        if not html.strip():
            return
        if self.blocks and self.blocks[-1]["type"] == RAW_HTML_BLOCK:
            self.blocks[-1]["value"] += "\n" + html
            return
        self.blocks.append({"type": RAW_HTML_BLOCK, "value": html})

    def add(self, block: dict) -> None:
        self.blocks.append(block)


MAX_DEPTH = 12


def _collect(nodes: List, collector: _BlockCollector, image_resolver, depth: int = 0) -> None:
    """Walk a level of the document, appending a block for each node it finds."""
    # Legacy markup nests wrappers, but not indefinitely; the guard stops a
    # pathological document from recursing without bound.
    if depth > MAX_DEPTH:
        for node in nodes:
            if isinstance(node, Tag):
                collector.add_raw_html(fragment_html(node))
        return

    for node in nodes:
        if _is_blank(node):
            continue
        if isinstance(node, NavigableString):
            # Bare text between blocks reads as its own paragraph.
            paragraph = _new_tag("p")
            paragraph.append(copy.copy(node))
            collector.add_rich_text(str(paragraph))
            continue

        button = _as_button(node)
        if button is not None:
            collector.add(button)
            continue

        if _is_transparent_container(node):
            _collect(_meaningful_children(node), collector, image_resolver, depth + 1)
            continue

        cell = _single_cell(node)
        if cell is not None:
            _collect(_meaningful_children(cell), collector, image_resolver, depth + 1)
            continue

        if node.name == "img":
            block = _image_block(node, image_resolver)
            if block is not None:
                collector.add(block)
            else:
                collector.add_raw_html(fragment_html(node))
            continue

        if _rich_text_name(node) is not None:
            _collect_flow_node(node, collector, image_resolver)
            continue

        collector.add_raw_html(fragment_html(node))


def _collect_flow_node(node: Tag, collector: _BlockCollector, image_resolver) -> None:
    """Emit blocks for a paragraph, heading, list or rule."""
    runs = _paragraph_runs(node)
    if runs is None:
        collector.add_raw_html(fragment_html(node))
        return

    if not runs:
        if not _is_rich_text_compatible(node):
            collector.add_raw_html(fragment_html(node))
        elif _has_visible_content(node):
            collector.add_rich_text(str(_sanitize_as_rich_text(node)))
        return

    # The node holds at least one image. Each image becomes its own block and
    # the prose on either side of it stays rich text, so the reading order of
    # the original page is kept.
    for kind, payload in runs:
        if kind == "image":
            block = _image_block(payload, image_resolver)
            if block is not None:
                collector.add(block)
            else:
                collector.add_raw_html(fragment_html(payload))
            continue
        fragment = _new_tag(node.name)
        for child in payload:
            fragment.append(copy.copy(child))
        if not _has_visible_content(fragment):
            continue
        if _is_rich_text_compatible(fragment):
            collector.add_rich_text(str(_sanitize_as_rich_text(fragment)))
        else:
            collector.add_raw_html(fragment_html(fragment))


def decompose_legacy_content(
    content: Tag,
    image_resolver: Optional[ImageResolver] = None,
) -> List[dict]:
    """
    Rebuild a legacy page's ``id="content"`` element as StreamField blocks.

    `content` may be that element, the whole page (see get_body_soup, which
    narrows either down to the article) or a fragment with no such element,
    which is taken to be the article in full. It is left untouched; the work happens on a copy. `image_resolver`
    turns a remote image URL into the primary key of a Wagtail image — pass
    None (or return None from it) to leave every <img> in a Raw HTML block.

    Returns a list of ``{"type": ..., "value": ...}`` dicts ready to be JSON
    encoded into a page's body, or an empty list when the content element
    held nothing importable, which the caller should treat as a reason to
    keep the original raw HTML.
    """
    working = copy.copy(content)
    body = get_body_soup(working)
    if body is None:
        # A fragment such as a blog post's excerpt, not a whole page: there is
        # no region to narrow down to, so the fragment is the article.
        body = working
    strip_extraneous_elements(body)
    strip_presentational_markup(body)

    collector = _BlockCollector()
    _collect(_meaningful_children(body), collector, image_resolver)
    return collector.blocks


# Elements that are content in their own right even when they hold no text.
_TEXTLESS_CONTENT_TAGS = ["img", "hr", "iframe", "video", "audio", "embed", "object", "svg"]


def do_blocks_have_visible_content(blocks: List[dict]) -> bool:
    """
    Whether any of `blocks` would show a reader something. A rich text or Raw
    HTML block holding only whitespace and empty tags does not count, but every
    other kind of block (an image, a button, and so on) does.
    """
    for block in blocks:
        if block["type"] not in (RICH_TEXT_BLOCK, RAW_HTML_BLOCK):
            return True
        fragment = BeautifulSoup(str(block["value"]), "html.parser")
        if fragment.find(_TEXTLESS_CONTENT_TAGS) or _has_visible_content(fragment):
            return True
    return False


class _Unit(NamedTuple):
    """
    The smallest piece of a block that `remove_duplicated_intro` compares: one
    top-level element of a text block, or a whole block of any other kind.
    """

    key: str
    block_index: int
    # The unit's own markup, for a text block. None for any other kind of block.
    markup: Optional[str]


_SPACE_AROUND_TAGS = re.compile(r"\s*(<[^>]+>)\s*")


def _comparable(markup: str) -> str:
    """`markup` with its whitespace made uniform, so that `<p>\nHello </p>`
    and `<p>Hello</p>` compare as equal."""
    return _SPACE_AROUND_TAGS.sub(r"\1", " ".join(markup.split()))


def _units(blocks: List[dict]) -> List[_Unit]:
    units: List[_Unit] = []
    for index, block in enumerate(blocks):
        if block["type"] not in (RICH_TEXT_BLOCK, RAW_HTML_BLOCK):
            key = f"{block['type']}:{json.dumps(block['value'], sort_keys=True)}"
            units.append(_Unit(key, index, None))
            continue
        for child in BeautifulSoup(str(block["value"]), "html.parser").contents:
            if isinstance(child, NavigableString) and not child.strip():
                continue
            markup = str(child)
            units.append(_Unit(f"{block['type']}:{_comparable(markup)}", index, markup))
    return units


def remove_duplicated_intro(body: List[dict], intro: List[dict]) -> Tuple[List[dict], int]:
    """
    Cut from the start of `body` whatever an `intro` already says.

    A blog post's intro is the start of its body, as the legacy blog page
    excerpted it, so the two begin identically. Comparing whole blocks would
    miss that, because consecutive paragraphs are merged into a single rich
    text block: the intro's block is only the first few paragraphs of the
    body's. So the comparison is between the top-level elements inside the
    blocks (each paragraph, list or image), and stops at the first one that
    differs. An element that the intro only quotes part of is not the same
    element, so it stays in the body in full.

    Returns the remaining body blocks and the number of elements cut. `body`
    and `intro` are left untouched.
    """
    body_units = _units(body)
    shared = 0
    for intro_unit, body_unit in zip(_units(intro), body_units):
        if intro_unit.key != body_unit.key:
            break
        shared += 1
    if not shared:
        return body, 0
    if shared == len(body_units):
        return [], shared

    first_remaining = body_units[shared]
    block = body[first_remaining.block_index]
    # Only a text block can be split, and only when the cut fell inside it
    # rather than on the boundary between it and the block before.
    cut_inside_block = body_units[shared - 1].block_index == first_remaining.block_index
    if cut_inside_block and first_remaining.markup is not None:
        separator = "\n" if block["type"] == RAW_HTML_BLOCK else ""
        head = [
            {
                "type": block["type"],
                "value": separator.join(
                    unit.markup
                    for unit in body_units[shared:]
                    if unit.block_index == first_remaining.block_index
                ),
            }
        ]
    else:
        head = [block]
    return [*head, *body[first_remaining.block_index + 1 :]], shared


def summarise(blocks: List[dict]) -> str:
    """A one-line tally of block types, for the import command's output."""
    counts: dict = {}
    for block in blocks:
        counts[block["type"]] = counts.get(block["type"], 0) + 1
    return ", ".join(f"{count} × {name}" for name, count in sorted(counts.items())) or "nothing"
