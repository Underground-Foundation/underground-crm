"""
Import G-NAF (Geocoded National Address File) data into PostgreSQL.

Creates the gnaf schema and address table with pg_trgm fuzzy-search indexes
and optional PostGIS geometry support, then downloads and loads the latest
G-NAF release from data.gov.au.

Usage:
    python manage.py import_gnaf
    python manage.py import_gnaf --zip-path /path/to/G-NAF_GDA2020.zip
    python manage.py import_gnaf --drop-first --batch-size 5000
    python manage.py import_gnaf --skip-download --zip-path /path/to/G-NAF_GDA2020.zip
"""

import re
import tempfile
import zipfile
from pathlib import Path
from time import perf_counter

import requests
from django.core.management.base import BaseCommand
from django.db import connection

# Map of target column name to list of possible header names in the PSV file.
# The first match wins; the order of alternatives reflects the most recent
# G-NAF format (Nov 2023+) followed by older naming conventions.
COLUMN_MAP: dict[str, list[str]] = {
    "gnaf_pid": ["ADDRESS_DETAIL_PID"],
    "street_number": ["NUMBER_FIRST", "NUMBER1"],
    "street_name": ["STREET_NAME", "STREETNAME"],
    "street_type": ["STREET_TYPE", "STREETTYPE"],
    "locality_name": ["LOCALITY_NAME", "LOCALITYNAME"],
    "state_abbreviation": ["STATE_ABBREVIATION", "STATE"],
    "postcode": ["LOCALITY_POSTCODE", "POSTCODE"],
    "latitude": ["LATITUDE", "LAT"],
    "longitude": ["LONGITUDE", "LON", "LONG"],
    "geocode_type": ["GEOCODE_TYPE", "GEOCODETYPE"],
    "confidence": ["CONFIDENCE"],
    "date_created": ["DATE_CREATED"],
    "date_retired": ["DATE_RETIRED"],
}

_DATASET_URL = "https://data.gov.au/data/dataset/geocoded-national-address-file-g-naf"


def _resolve_headers(headers: list[str]) -> dict[str, int]:
    """Map target column names to their 0-based index in a PSV header row.

    Returns a dict of target_name -> column_index for every column in
    COLUMN_MAP that was found in *headers*.
    """
    upper_headers = [h.strip().upper() for h in headers]
    resolved: dict[str, int] = {}
    for target, variants in COLUMN_MAP.items():
        for variant in variants:
            try:
                idx = upper_headers.index(variant.upper())
            except ValueError:
                continue
            resolved[target] = idx
            break
    return resolved


def _build_sla(row: list[str], col: dict[str, int]) -> str:
    """Build the searchable single-line address string from a PSV row."""
    parts = []
    for key in ("street_number", "street_name", "street_type", "locality_name",
                 "state_abbreviation", "postcode"):
        idx = col.get(key)
        if idx is not None and idx < len(row):
            val = row[idx].strip()
            if val:
                parts.append(val)
    return " ".join(parts)


def _find_download_url() -> str:
    """Scrape the data.gov.au dataset page for the latest G-NAF GDA2020 ZIP URL."""
    resp = requests.get(_DATASET_URL, timeout=60)
    resp.raise_for_status()
    html = resp.text
    # Look for links ending in .zip that mention G-NAF and GDA2020.
    pattern = re.compile(
        r'href=["\']([^"\']*g-?naf[^"\']*gda2020[^"\']*\.zip)["\']',
        re.IGNORECASE,
    )
    matches = pattern.findall(html)
    if not matches:
        raise RuntimeError(
            "Could not find a G-NAF GDA2020 download link on the dataset page. "
            "Try downloading manually and passing --zip-path."
        )
    url = matches[0]
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("/"):
        url = "https://data.gov.au" + url
    return url


def _pg_trgm_available(cursor) -> bool:
    """Return True if pg_trgm extension can be created."""
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        return True
    except Exception:
        return False


def _postgis_available(cursor) -> bool:
    """Return True if PostGIS extension can be created."""
    try:
        cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        return True
    except Exception:
        return False


class Command(BaseCommand):
    help = "Download and import G-NAF address data into PostgreSQL."

    def add_arguments(self, parser):
        parser.add_argument(
            "--zip-path",
            type=str,
            default=None,
            help="Path to a local G-NAF ZIP file. If omitted, the latest release is downloaded.",
        )
        parser.add_argument(
            "--schema",
            type=str,
            default="gnaf",
            help="PostgreSQL schema name (default: gnaf).",
        )
        parser.add_argument(
            "--table",
            type=str,
            default="address",
            help="Table name (default: address).",
        )
        parser.add_argument(
            "--drop-first",
            action="store_true",
            default=False,
            help="Drop the existing table before importing.",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=1000,
            help="Rows per INSERT batch (default: 1000).",
        )
        parser.add_argument(
            "--skip-download",
            action="store_true",
            default=False,
            help="Skip download; only create schema/indexes or import from an existing ZIP.",
        )

    def handle(self, *args, **options):
        zip_path: str | None = options["zip_path"]
        schema: str = options["schema"]
        table: str = options["table"]
        drop_first: bool = options["drop_first"]
        batch_size: int = options["batch_size"]
        skip_download: bool = options["skip_download"]

        self.stdout.write(f"Target: {schema}.{table}")
        if batch_size <= 0:
            batch_size = 1000

        # ------------------------------------------------------------------
        # 1. Schema & table creation
        # ------------------------------------------------------------------
        with connection.cursor() as cursor:
            cursor.execute(
                f'CREATE SCHEMA IF NOT EXISTS "{schema}"'
            )
            full_table = f'"{schema}"."{table}"'

            if drop_first:
                self.stdout.write(f"Dropping table {full_table} if it exists…")
                cursor.execute(f"DROP TABLE IF EXISTS {full_table}")

            cursor.execute(f"""
                CREATE TABLE IF NOT EXISTS {full_table} (
                    gnaf_pid TEXT PRIMARY KEY,
                    street_number TEXT,
                    street_name TEXT,
                    street_type TEXT,
                    locality_name TEXT,
                    state_abbreviation TEXT,
                    postcode TEXT,
                    latitude NUMERIC(9,6),
                    longitude NUMERIC(9,6),
                    geocode_type TEXT,
                    confidence INTEGER,
                    sla TEXT NOT NULL,
                    date_created DATE,
                    date_retired DATE
                )
            """)

            has_pg_trgm = _pg_trgm_available(cursor)
            self.stdout.write(
                f"pg_trgm: {'available' if has_pg_trgm else 'not available'}"
            )

            has_postgis = _postgis_available(cursor)
            self.stdout.write(
                f"PostGIS: {'available' if has_postgis else 'not available'}"
            )

        # ------------------------------------------------------------------
        # 2. Download ZIP (unless skipped or --zip-path provided)
        # ------------------------------------------------------------------
        if zip_path:
            zip_file = Path(zip_path)
            if not zip_file.exists():
                self.stderr.write(f"ZIP file not found: {zip_path}")
                return
            self.stdout.write(f"Using local ZIP: {zip_file}")
        elif skip_download:
            self.stdout.write("--skip-download set; no ZIP to process.")
            zip_file = None
        else:
            self.stdout.write("Finding download URL on data.gov.au…")
            url = _find_download_url()
            self.stdout.write(f"Downloading: {url}")
            tmpdir = Path(tempfile.mkdtemp(prefix="gnaf_"))
            zip_file = tmpdir / "gnaf.zip"
            with requests.get(url, stream=True, timeout=300) as resp:
                resp.raise_for_status()
                with open(zip_file, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
            self.stdout.write(f"Downloaded to {zip_file}")

        # ------------------------------------------------------------------
        # 3. Import from ZIP
        # ------------------------------------------------------------------
        if zip_file:
            self._import_from_zip(
                zip_file, schema, table, batch_size, has_postgis,
            )

        # ------------------------------------------------------------------
        # 4. Indexes
        # ------------------------------------------------------------------
        self._create_indexes(schema, table, has_pg_trgm, has_postgis)

        self.stdout.write(self.style.SUCCESS("Done."))

    def _import_from_zip(
        self,
        zip_path: Path,
        schema: str,
        table: str,
        batch_size: int,
        has_postgis: bool,
    ):
        full_table = f'"{schema}"."{table}"'
        with zipfile.ZipFile(zip_path, "r") as zf:
            psv_files = [
                n for n in zf.namelist()
                if re.search(r"address_detail.*\.psv", n, re.IGNORECASE)
            ]
            if not psv_files:
                self.stderr.write("No ADDRESS_DETAIL PSV files found in the ZIP.")
                return

            self.stdout.write(f"Found {len(psv_files)} ADDRESS_DETAIL file(s).")

            total_rows = 0

            for psv_name in sorted(psv_files):
                t0 = perf_counter()
                file_rows = self._import_psv_file(
                    zf, psv_name, full_table, batch_size,
                )
                elapsed = perf_counter() - t0
                total_rows += file_rows
                self.stdout.write(
                    f"  {psv_name}: {file_rows} rows in {elapsed:.1f}s "
                    f"({file_rows / elapsed:.0f} rows/s)"
                )

            self.stdout.write(f"Total imported: {total_rows} rows.")

            if has_postgis and total_rows:
                self._populate_geometry(full_table)

    def _import_psv_file(
        self,
        zf: zipfile.ZipFile,
        name: str,
        full_table: str,
        batch_size: int,
    ) -> int:
        """Process a single PSV file and return the number of rows imported."""
        with zf.open(name, "r") as f:
            text = f.read().decode("utf-8-sig", errors="replace")
        lines = text.splitlines()
        if not lines:
            return 0

        # Parse the header row.
        header = [h.strip() for h in lines[0].split("|")]
        col = _resolve_headers(header)
        missing = [k for k in COLUMN_MAP if k not in col and k != "geocode_type"]
        if missing and "gnaf_pid" in missing:
            self.stderr.write(
                f"  [warn] {name}: cannot find required columns {missing}; skipping."
            )
            return 0

        data_lines = lines[1:]  # skip header
        rows_imported = 0
        batch: list[tuple] = []
        skipped = 0

        insert_sql = f"""
            INSERT INTO {full_table} (
                gnaf_pid, street_number, street_name, street_type,
                locality_name, state_abbreviation, postcode,
                latitude, longitude, geocode_type, confidence,
                sla, date_created, date_retired
            ) VALUES (
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s
            ) ON CONFLICT (gnaf_pid) DO NOTHING
        """

        for line in data_lines:
            if not line.strip():
                continue
            parts = line.split("|")

            # Ensure we have at least enough columns.
            gnaf_pid = parts[col["gnaf_pid"]].strip() if col.get("gnaf_pid") is not None else ""
            if not gnaf_pid:
                skipped += 1
                continue

            lat_idx = col.get("latitude")
            lon_idx = col.get("longitude")
            lat_str = parts[lat_idx].strip() if lat_idx is not None and lat_idx < len(parts) else ""
            lon_str = parts[lon_idx].strip() if lon_idx is not None and lon_idx < len(parts) else ""

            # Parse numeric values.
            lat: float | None = None
            lon: float | None = None
            if lat_str and lon_str:
                try:
                    lat = float(lat_str)
                    lon = float(lon_str)
                except ValueError:
                    pass

            # Parse confidence.
            conf_idx = col.get("confidence")
            conf: int | None = None
            if conf_idx is not None and conf_idx < len(parts):
                try:
                    conf = int(parts[conf_idx].strip())
                except (ValueError, TypeError):
                    pass

            # Build sla.
            sla = _build_sla(parts, col)

            def _val(key: str) -> str:
                idx = col.get(key)
                if idx is not None and idx < len(parts):
                    return parts[idx].strip()
                return ""

            street_number = _val("street_number")
            street_name = _val("street_name")
            street_type = _val("street_type")
            locality_name = _val("locality_name")
            state_abbreviation = _val("state_abbreviation")
            postcode = _val("postcode")
            geocode_type = _val("geocode_type")

            date_created = _val("date_created") or None
            date_retired = _val("date_retired") or None

            batch.append((
                gnaf_pid, street_number or None, street_name or None,
                street_type or None, locality_name or None,
                state_abbreviation or None, postcode or None,
                lat, lon, geocode_type or None, conf,
                sla, date_created, date_retired,
            ))

            if len(batch) >= batch_size:
                self._flush_batch(insert_sql, batch)
                rows_imported += len(batch)
                batch = []

        if batch:
            self._flush_batch(insert_sql, batch)
            rows_imported += len(batch)

        if skipped:
            self.stderr.write(f"  [warn] {name}: skipped {skipped} row(s) with no G-NAF PID.")

        return rows_imported

    def _flush_batch(self, sql: str, batch: list[tuple]):
        with connection.cursor() as cursor:
            cursor.executemany(sql, batch)

    def _populate_geometry(self, full_table: str):
        self.stdout.write("Populating geometry column…")
        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER TABLE {full_table} ADD COLUMN IF NOT EXISTS geom geometry(Point, 7844)"
            )
            cursor.execute(
                f"UPDATE {full_table} SET geom = "
                f"ST_SetSRID(ST_MakePoint(longitude, latitude), 7844) "
                f"WHERE longitude IS NOT NULL AND latitude IS NOT NULL"
            )

    def _create_indexes(
        self,
        schema: str,
        table: str,
        has_pg_trgm: bool,
        has_postgis: bool,
    ):
        full_table = f'"{schema}"."{table}"'
        self.stdout.write("Creating indexes…")

        with connection.cursor() as cursor:
            if has_pg_trgm:
                idx_name = f"idx_{schema}_{table}_sla_trgm"
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {idx_name} "
                    f"ON {full_table} USING GIN (sla gin_trgm_ops)"
                )
                self.stdout.write(f"  Created GIN trgm index on sla.")

            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{schema}_{table}_locality "
                f"ON {full_table} (locality_name)"
            )
            self.stdout.write("  Created index on locality_name.")

            cursor.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{schema}_{table}_postcode "
                f"ON {full_table} (postcode)"
            )
            self.stdout.write("  Created index on postcode.")

            if has_postgis:
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS idx_{schema}_{table}_geom "
                    f"ON {full_table} USING GIST (geom)"
                )
                self.stdout.write("  Created GIST index on geom.")
