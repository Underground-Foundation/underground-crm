"""Template filters that make images in an email's body behave in mail clients.

Load the library in an email template with `{% load email_images %}`.
"""

import re
from typing import Optional

from bs4 import BeautifulSoup
from django import template
from django.utils.safestring import SafeString, mark_safe

register = template.Library()

# A percentage width in an inline style, such as the "width: 50%" that IntrinsicFormat
# writes.  The lookbehind keeps it from matching the end of "max-width".
_PERCENTAGE_WIDTH = re.compile(r"(?<![\w-])width\s*:\s*(\d+(?:\.\d+)?)%")
_FULL_WIDTH_PERCENT = 100.0


def _get_percentage_width(style: Optional[str]) -> float:
    """Return the percentage of its container that an image's inline style asks it to fill,
    or 100 when the style sets no percentage width."""
    match = _PERCENTAGE_WIDTH.search(style or "")
    return float(match.group(1)) if match else _FULL_WIDTH_PERCENT


@register.filter(is_safe=True)
def cap_image_widths(html: str, column_width: int) -> SafeString:
    """Cap the `width` attribute of every image in `html` at the email's column width.

    Outlook for Windows ignores CSS max-width on images and sizes them from the `width`
    attribute alone, so an image whose attribute holds its natural width (2000 pixels, say)
    would overflow the email.  Only the template knows how wide its column is, which is why
    this is a filter for the template to apply around the email's body:

        {% filter cap_image_widths:550 %}{% include_block body %}{% endfilter %}

    An image whose inline style asks for a percentage of its container (such as a
    half-width image's "width: 50%") is capped at that percentage of the column.  An image
    that is already narrower than its cap is left at its natural width, so that it is never
    upscaled.  Widths that are not a whole number of pixels are left alone.

    A capped image loses its `height` attribute.  Otherwise a mail client that strips the
    inline styles when quoting the email in a reply, and shrinks the image to fit, would
    keep it at the old height and stretch it vertically.
    """
    soup = BeautifulSoup(str(html), "html.parser")
    for img in soup.find_all("img"):
        width = img.get("width", "")
        if not width or not width.isdigit():
            continue
        cap = int(int(column_width) * _get_percentage_width(img.get("style")) / _FULL_WIDTH_PERCENT)
        if int(width) > cap:
            img["width"] = str(cap)
            if img.has_attr("height"):
                del img["height"]
    return mark_safe(str(soup))
