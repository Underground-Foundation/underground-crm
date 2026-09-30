"""
Internalizing images referenced by legacy pages into the Wagtail image library.

underground_crm/legacy_html.py rebuilds a legacy page out of Wagtail blocks,
and the Image block it wants to use stores an image chosen from the library —
not a remote URL. So each ``<img src="...">`` pointing at a legacy asset host,
encountered during a decomposition, has to be fetched once and registered as
a Wagtail image before the block can point at it.

How a source is judged, deduplicated and adopted from the media storage is the
same for every kind of file, and described in underground_crm/legacy_files.py.
Here, an image that Wagtail cannot hold (an SVG) is left where it is, as raw
HTML, and a response that is not an image is not stored as one.
"""

from io import BytesIO
from pathlib import PurePosixPath
from typing import Optional

from django.core.files.images import ImageFile

from underground_crm.legacy_files import (  # pylint: disable=unused-import
    DEFAULT_TIMEOUT,
    RemoteFileResolver,
    legacy_asset_urls_from_env,
    satisfactory_image_domains_from_env,
)

# Wagtail rejects an upload whose extension is not in WAGTAILIMAGES_EXTENSIONS,
# which does not include SVG unless a project opts in. Rather than guess, these
# are left as raw HTML.
UNSUPPORTED_SUFFIXES = frozenset({".svg", ".svgz"})


class RemoteImageResolver(RemoteFileResolver):
    """
    Fetches remote images and returns the primary key of the Wagtail image
    holding each one, for use as legacy_html's `image_resolver`.
    """

    kind = "image"
    left_as = "left as raw HTML"

    @staticmethod
    def _image_model():
        from wagtail.images import get_image_model

        return get_image_model()

    def _model(self):
        return self._image_model()

    def _skip_reason(self, filename: str) -> Optional[str]:
        if PurePosixPath(filename).suffix.lower() in UNSUPPORTED_SUFFIXES:
            return "unsupported image type"
        return None

    def _accepts(self, content_type: str) -> bool:
        return not content_type or content_type.startswith("image/")

    def _build(self, model, content: bytes, filename: str, title_hint: str):
        """
        width and height are not editable and never filled in by save(), so
        they are read off the file here — Wagtail normally gets them from the
        upload form, which an import has no equivalent of.
        """
        uploaded = ImageFile(BytesIO(content), name=filename)
        if uploaded.width is None or uploaded.height is None:
            # Django reports unreadable image data by returning no dimensions
            # rather than raising, and Wagtail's width/height columns are not
            # nullable — so this has to be caught here or it becomes an
            # IntegrityError halfway through the import.
            raise ValueError("no image dimensions could be read")
        return model(
            title=self._title(filename, title_hint),
            file=uploaded,
            width=uploaded.width,
            height=uploaded.height,
        )
