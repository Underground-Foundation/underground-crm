"""
Internalizing documents linked from legacy pages into the Wagtail document library.

A legacy page links its uploaded files (``<a href=".../submission.pdf">``) from a
legacy asset host. Each such link is followed once, the file registered as a
Wagtail document (in the media storage, like images), and the link pointed at
the stored file instead. How a source is judged, deduplicated and adopted from
the media storage is described in underground_crm/legacy_files.py.
"""

from pathlib import PurePosixPath
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from django.core.files.base import ContentFile

from underground_crm.legacy_files import RemoteFileResolver

# The extensions for non-image files which are to be treated as internalizable documents.
DOCUMENT_EXTENSIONS = frozenset({"pdf", "ics"})


class RemoteDocumentResolver(RemoteFileResolver):
    """
    Fetches remote documents and returns the primary key of the Wagtail document
    holding each one. `title_hint` is the text of the link that led to it.
    """

    kind = "document"

    @staticmethod
    def _document_model():
        from wagtail.documents import get_document_model

        return get_document_model()

    def _model(self):
        return self._document_model()

    def _accepts(self, content_type: str) -> bool:
        # A dead attachment often answers with a page of HTML rather than an error.
        return content_type != "text/html"

    def _build(self, model, content: bytes, filename: str, title_hint: str):
        return model(
            title=self._title(filename, title_hint), file=ContentFile(content, name=filename)
        )

    def url_of(self, pk) -> Optional[str]:
        """Where the stored document is served from."""
        document = self._document_model().objects.filter(pk=pk).first()
        return document.file.url if document is not None else None


def is_document_url(url: str) -> bool:
    """Whether `url` is a link to one of the documents legacy pages upload."""
    suffix = PurePosixPath(urlparse(url).path).suffix.lower().lstrip(".")
    return suffix in DOCUMENT_EXTENSIONS


def rewrite_document_links(soup: BeautifulSoup, resolver: RemoteDocumentResolver) -> int:
    """
    Point every link to a legacy document at the stored document instead, in
    place, and return how many were changed. A link the resolver cannot bring
    across (or that it declines, being to somewhere else entirely) is left alone.
    """
    changed = 0
    for link in soup.find_all("a", href=True):
        href = link["href"].strip()
        if not is_document_url(href):
            continue
        pk = resolver(href, " ".join(link.get_text().split()))
        new_url = resolver.url_of(pk) if pk is not None else None
        if new_url:
            link["href"] = new_url
            changed += 1
    return changed
