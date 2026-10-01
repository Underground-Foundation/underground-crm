"""
A management command to bring the assets referenced by fetched legacy pages across.

Usage:
    python manage.py migrate_assets --domain <domain>             # bring across, leave pages alone
    python manage.py migrate_assets --domain <domain> --replace   # also rewrite src/href
    python manage.py migrate_assets --domain <domain> --dry-run   # report only, touch nothing

Images which Wagtail can hold will become Wagtail images, and PDFs become Wagtail documents
(in the media storage and their libraries). Other assets, such as SVGs, are put in
place by the asset uploader (see build_asset_uploader). The determination of which
URLs are indicative of legacy assets is defined in underground_crm/legacy_assets.py.
"""

import logging
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from underground_crm.legacy_assets import (
    AssetUploader,
    LegacyAssetMigrator,
    build_legacy_session,
    store_in_media,
)
from underground_crm.legacy_documents import RemoteDocumentResolver
from underground_crm.legacy_images import RemoteImageResolver, legacy_asset_urls_from_env

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Bring the images and documents referenced by fetched legacy pages across."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--domain",
            required=True,
            help="Domain directory of fetched pages to scan (e.g. fusionparty.org.au).",
        )
        parser.add_argument(
            "--replace",
            action="store_true",
            help="Rewrite each asset's src/href to its new location in the fetched pages.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be brought across without fetching or writing anything.",
        )

    def build_asset_uploader(self, options) -> AssetUploader:
        """
        What puts the assets that Wagtail's libraries cannot hold (an SVG) where
        they will be served from. A deployment whose files are served
        from somewhere the media storage does not reach overrides this.
        """
        return store_in_media

    def handle(self, *args, **options) -> None:
        site_dir = Path(options["domain"])
        if not site_dir.is_dir():
            raise CommandError(f"{site_dir} is not a directory")

        session = build_legacy_session(os.environ.get("LEGACY_USER_AGENT", ""))
        asset_urls = legacy_asset_urls_from_env()
        resolvers = []
        for resolver_class in (RemoteImageResolver, RemoteDocumentResolver):
            resolver = resolver_class(
                base_url=settings.WAGTAILADMIN_BASE_URL,
                legacy_asset_urls=asset_urls,
                # Everything under LEGACY_ASSET_URLS is fetched, wherever it is hosted.
                satisfactory_image_domains=(),
                stdout=self.stdout,
            )
            resolver.session = session
            resolvers.append(resolver)
        image_resolver, document_resolver = resolvers
        migrator = LegacyAssetMigrator(
            image_resolver,
            document_resolver,
            asset_urls,
            upload_asset=self.build_asset_uploader(options),
            session=session,
            replace=options["replace"],
            dry_run=options["dry_run"],
        )

        html_files = sorted(site_dir.rglob("*.html"))
        self.stdout.write(f"Scanning {len(html_files)} HTML files under {site_dir}")
        totals = {"images": 0, "documents": 0, "commented": 0, "other": 0}
        for index, path in enumerate(html_files, start=1):
            stats = migrator.process_file(path, site_dir)
            for kind, count in stats.items():
                totals[kind] += count
            if any(stats.values()):
                self.stdout.write(
                    f"[{index}/{len(html_files)}] {path.relative_to(site_dir)}: "
                    f"{stats['images']} image(s), {stats['documents']} document(s), "
                    f"{stats['commented']} commented out, {stats['other']} other"
                )

        self.stdout.write(
            f"Done. {totals['images']} image reference(s), {totals['documents']} document "
            f"reference(s), {totals['commented']} commented-out reference(s), {totals['other']} "
            f"other (unmigrated) reference(s). {len(migrator.migrated) - migrator.failed} unique "
            f"asset(s) brought across, {migrator.failed} failed. "
            f"Images: {image_resolver.get_summary()}. "
            f"Documents: {document_resolver.get_summary()}."
        )
        if migrator.failed:
            raise CommandError(f"{migrator.failed} asset(s) could not be brought across.")
