"""Build apps/sidecar/data/ebird-codes.json ({scientific name: species code}) from an eBird Taxonomy CSV.

Usage: python scripts/build_ebird_codes.py eBird_taxonomy_v2025.csv
Source: eBird/Clements taxonomy, Cornell Lab of Ornithology (cite the version used).
"""
import csv
import json
import sys
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / 'apps/sidecar/data/ebird-codes.json'


def column(header, *names):
    found = next((h for h in header if h.strip().upper().replace(' ', '_') in names), None)
    if found is None:
        sys.exit(f'Missing column: {" or ".join(names)}')
    return found


def main(path):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        rows = csv.DictReader(stream)
        header = rows.fieldnames or []
        name = column(header, 'SCI_NAME', 'SCIENTIFIC_NAME')
        code = column(header, 'SPECIES_CODE')
        category = next((h for h in header if h.strip().upper() == 'CATEGORY'), None)
        codes = {row[name].strip(): row[code].strip() for row in rows
                 if row[name].strip() and row[code].strip() and (category is None or row[category].strip().lower() == 'species')}
    OUTPUT.write_text(json.dumps(dict(sorted(codes.items())), ensure_ascii=False, indent=0) + '\n', encoding='utf-8')
    print(f'{len(codes)} species -> {OUTPUT}')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
