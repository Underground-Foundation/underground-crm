import logging
from typing import Optional

from django.utils.html import escape
from wagtail.images.formats import Format, register_image_format, unregister_image_format
from wagtail.images.models import AbstractImage
from wagtail.images.shortcuts import get_rendition_or_not_found

logger = logging.getLogger(__name__)


class IntrinsicFormat(Format):
    """Image format that renders at the image's natural dimensions, capped at the container
    width.  The filter spec should be 'original' so the file is never upscaled server-side;
    the inline style prevents it from overflowing its container client-side.

    The markup is written to survive mail clients as well as browsers, since the same rich
    text is sent in email campaigns:

    - The cap is expressed as `width: N%; max-width: <natural width>px` rather than with
      CSS min(), which Outlook and many webmail sanitizers do not support, and which makes
      them discard the whole declaration.
    - `height: auto` is always set, and no `height` attribute is emitted.  Mail clients that
      strip inline styles when quoting a message in a reply still shrink a wide image to
      fit, and if a `height` attribute were left behind, the image would keep its full
      height and be stretched vertically.
    - The `width` attribute is the image's natural width.  Outlook for Windows ignores CSS
      max-width and sizes the image from that attribute alone, so an email template should
      cap it at the template's column width with the `cap_image_widths` filter (see
      underground_email/templatetags/email_images.py).  Only the template knows how wide
      its column is.

    Set centered=True to add display:block and auto side margins, centering the image within
    its container.  Left- and right-aligned formats leave centering unset so that their CSS
    float classes take effect."""

    def __init__(
        self,
        name: str,
        label: str,
        classname: str,
        filter_spec: str,
        centered: bool = False,
        width_fraction: float = 1.0,
    ) -> None:
        super().__init__(name, label, classname, filter_spec)
        self.centered = centered
        self.width_fraction = width_fraction

    def image_to_html(self, image: AbstractImage, alt_text: Optional[str], extra_attributes=None):
        if extra_attributes is None:
            extra_attributes = {}
        rendition = get_rendition_or_not_found(image, self.filter_spec)
        extra_attributes["alt"] = escape(alt_text)
        if self.classname:
            extra_attributes["class"] = escape(self.classname)
        pct = int(self.width_fraction * 100)
        style = f"width: {pct}%; max-width: {rendition.width}px; height: auto"
        if self.centered:
            style = f"display: block; margin: 0 auto; {style}"
        extra_attributes["style"] = style
        extra_attributes["width"] = str(rendition.width)
        extra_attributes["height"] = None
        logger.info(
            "%s shall be rendered at intrinsic width %s px", image.filename, rendition.width
        )
        return rendition.img_tag(extra_attributes)


# Replace Wagtail's built-in formats (which use width-N and upscale small images).
# IntrinsicFormat serves the original file and caps its display width at whichever is
# smaller: the intended fraction of the container, or the image's natural width.
unregister_image_format("fullwidth")
unregister_image_format("left")
unregister_image_format("right")
register_image_format(
    IntrinsicFormat(
        "fullwidth", "Full width", "richtext-image full-width", "original", centered=True
    )
)
register_image_format(
    IntrinsicFormat(
        "w-50",
        "Half width",
        "richtext-image half-width",
        "original",
        centered=True,
        width_fraction=0.5,
    )
)
register_image_format(IntrinsicFormat("left", "Left-aligned", "richtext-image left", "original"))
register_image_format(IntrinsicFormat("right", "Right-aligned", "richtext-image right", "original"))
