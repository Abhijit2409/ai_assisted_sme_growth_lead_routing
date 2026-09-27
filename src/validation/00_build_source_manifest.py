"""
Build / verify data/source_manifest.csv: provenance and checksums for every file
under data/raw.

- New files are appended with their sha256.
- Recorded bytes, sha256 and retrieval_date are NEVER overwritten. A size or
  checksum mismatch is reported and the script exits non-zero, so the manifest
  stays a durable reference for "raw data unchanged".
- Verified provenance (VERIFIED_PROVENANCE) is written only into fields that are
  empty or hold the placeholder note.
- The manifest lives outside data/raw (which is gitignored) so it can be committed.

Run with:
    C:/Users/abhij/anaconda3/python.exe src/validation/00_build_source_manifest.py
"""

from pathlib import Path
from datetime import datetime
import csv
import hashlib
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]   # project root, independent of the working directory
RAW_DIR = ROOT / "data" / "raw"
MANIFEST_FILE = ROOT / "data" / "source_manifest.csv"

PLACEHOLDER_NOTE = "Provenance to be recorded when this source is validated."

# Provenance verified on official pages during source validation (Phase C for ISED).
# Applied only to fields that are empty (or hold the placeholder note).
VERIFIED_PROVENANCE = {
    "ised_sme_financing_2023": {
        "table_title": "Survey on Financing and Growth of Small and Medium Enterprises, 2023 - Data tables",
        "table_page_url": ("https://open.canada.ca/data/en/dataset/c3c9f361-c7d3-4c87-8be0-64c2b62521ee/"
                           "resource/56f10b18-2aeb-4734-b0ad-8450bdf25d08"),
        "metadata_reference_period": "2023 (survey reference year; collection 2024-02-22 to 2024-06-24)",
        "provenance_note": (
            "Produced by Statistics Canada in collaboration with ISED Small Business Branch; data released "
            "2025-02-20 (cover page). Open Government Portal resource 'Tables: Survey on Financing and Growth of "
            "SMEs 2023' (XLSX, 2,482,697 bytes): published 2025-04-17, data last updated 2025-06-24. Survey: "
            "Statistics Canada IMDB record 2941. Retrieval date was not recorded; file_modified_local is the best "
            "available proxy."),
    },
}

COLUMNS = [
    "source_id", "relative_path", "file_role", "bytes", "sha256",
    "file_modified_local", "table_title", "table_page_url",
    "metadata_reference_period", "retrieval_date", "provenance_note",
]


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def statcan_cube_info(source_dir):
    """Title, table URL and reference period from a StatCan *_MetaData.csv, if present."""
    meta = next(source_dir.rglob("*_MetaData.csv"), None)
    if meta is None:
        return {}
    with open(meta, encoding="utf-8-sig", newline="") as f:
        header, first = next(csv.reader(f)), None
        f.seek(0)
        reader = csv.reader(f)
        next(reader)
        first = next(reader)
    cube = dict(zip(header, first))
    return {
        "table_title": cube.get("Cube Title", ""),
        "table_page_url": cube.get("URL", ""),
        "metadata_reference_period": f"{cube.get('Start Reference Period', '')} to {cube.get('End Reference Period', '')}",
    }


def file_role(path):
    name = path.name.lower()
    if name.endswith("_metadata.csv"):
        return "metadata"
    if name.endswith(".zip"):
        return "original_download"
    if name.endswith(".csv"):
        return "data"
    if name.endswith(".xlsx"):
        return "workbook"
    return "other"


def main():
    existing = pd.read_csv(MANIFEST_FILE, dtype=str).fillna("") if MANIFEST_FILE.exists() \
        else pd.DataFrame(columns=COLUMNS)
    known = {r["relative_path"]: r for _, r in existing.iterrows()}

    new_rows, mismatches, filled = [], [], []
    for path in sorted(p for p in RAW_DIR.rglob("*") if p.is_file()):
        rel = path.relative_to(ROOT).as_posix()
        size = path.stat().st_size
        digest = sha256_file(path)

        if rel in known:
            ok = int(known[rel]["bytes"]) == size and known[rel]["sha256"] == digest
            print(f"  [{'OK' if ok else 'MISMATCH'}] {rel}")
            if not ok:
                mismatches.append(rel)
            # Fill verified provenance into empty fields only (never bytes, sha256 or retrieval_date).
            source_id = known[rel]["source_id"]
            for field, value in VERIFIED_PROVENANCE.get(source_id, {}).items():
                current = existing.loc[existing.relative_path == rel, field].iloc[0]
                if current in ("", PLACEHOLDER_NOTE):
                    existing.loc[existing.relative_path == rel, field] = value
                    filled.append(f"{rel}: {field}")
            continue

        source_dir = RAW_DIR / path.relative_to(RAW_DIR).parts[0]
        info = statcan_cube_info(source_dir)
        new_rows.append({
            "source_id": source_dir.name,
            "relative_path": rel,
            "file_role": file_role(path),
            "bytes": size,
            "sha256": digest,
            "file_modified_local": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
            "table_title": info.get("table_title", ""),
            "table_page_url": info.get("table_page_url", ""),
            "metadata_reference_period": info.get("metadata_reference_period", ""),
            "retrieval_date": "not recorded",
            "provenance_note": (
                "Title, URL and reference period read from the table's _MetaData.csv. "
                "Retrieval date was not recorded at download time; file_modified_local of "
                "the original_download row is the best available proxy."
                if info else PLACEHOLDER_NOTE
            ),
        })
        print(f"  [ADDED] {rel}")

    for f in filled:
        print(f"  [PROVENANCE FILLED] {f}")
    if new_rows or filled:
        out = pd.concat([existing, pd.DataFrame(new_rows)], ignore_index=True)[COLUMNS]
        out.to_csv(MANIFEST_FILE, index=False)
        print(f"Manifest written: {MANIFEST_FILE} ({len(out)} files)")
    else:
        print("Manifest unchanged.")

    if mismatches:
        print(f"ERROR: {len(mismatches)} raw file(s) differ from the manifest: {mismatches}")
        sys.exit(1)


if __name__ == "__main__":
    main()
