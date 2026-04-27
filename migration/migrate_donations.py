#!/usr/bin/env python3
"""
Parse a NationBuilder financial CSV export and emit newline-delimited JSON.

NationBuilder exports are UTF-8, but Microsoft tools (Excel, etc.) must have
re-saved them as Windows-1252/Latin-1 without proper conversion, mangling any
multi-byte character. For example, "ö" (UTF-8 bytes 0xC3 0xB6) gets stored
literally in the file and then read back as the two Latin-1 characters "Ã¶".
This program therefore reads the file as Latin-1 and repairs every string field.

Usage:
  python migrate_donations.py nationbuilder_finiancial_sample.csv
  python migrate_donations.py path/to/export.csv > donations.ndjson

Outputs newline-delimited JSON to stdout. Progress and warnings go to stderr.
"""

import argparse
import csv
import json
import sys


def fix_encoding(text):
    """
    Repair a string whose UTF-8 bytes were misread as Latin-1.

    If the string contains no mojibake (either because it is pure ASCII or
    because the file was already read correctly), the encode/decode round-trip
    raises an exception and the original string is returned unchanged.
    """
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return text


def fix_row(row):
    """Apply fix_encoding to every string value in a CSV row dict."""
    return {
        key: fix_encoding(value) if isinstance(value, str) else value for key, value in row.items()
    }


def parse_csv(path):
    """Read the NationBuilder CSV and yield cleaned row dicts."""
    # Open as Latin-1 so that no bytes are lost before we repair them.
    with open(path, encoding="latin-1", newline="") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, start=1):
            yield i, fix_row(row)


# ---- Main ----

parser = argparse.ArgumentParser(
    description="Parse a NationBuilder financial CSV and emit newline-delimited JSON."
)
parser.add_argument("csv_file", help="Path to the NationBuilder financial CSV export.")
args = parser.parse_args()

count = 0
for i, row in parse_csv(args.csv_file):
    print(json.dumps(row))
    count += 1
    if count % 500 == 0:
        print(f"  [info] processed {count} rows...", file=sys.stderr)

print(f"Done. Total rows: {count}", file=sys.stderr)
