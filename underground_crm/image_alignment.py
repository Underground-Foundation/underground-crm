"""
The stored values of the Image block's ``alignment`` choice.

This is a plain standard-library enum in a module of its own, importing no
Django or Wagtail, so that both the block definition
(underground_crm.models.pages, which adds the translatable labels) and the
legacy page importer (underground_crm.legacy_html, which must stay free of
Django and Wagtail) can name the same choices without either depending on the
other.
"""

from enum import StrEnum


class ImageAlignment(StrEnum):
    FULL_WIDTH = "full-width"
    LEFT = "left"
    RIGHT = "right"
    HALF_WIDTH = "w-50"  # Centered


DEFAULT_IMAGE_ALIGNMENT = ImageAlignment.FULL_WIDTH
