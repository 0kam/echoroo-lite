"""Build the species -> family table used to roll out-of-region AI candidates up to a family.

Source: the taxonomy database shipped in the perch-hoplite wheel (Apache-2.0), which maps iNaturalist 2024 and
eBird 2021/2022 species to families. Usage:
    python scripts/build_families.py path/to/perch_hoplite/taxonomy/taxonomy_database.json
Japanese family names come from FAMILY_JA (Checklist of Japanese Birds, 8th edition); families without one are
shown by their scientific name.
"""
import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'apps/sidecar/data'
OUTPUT = DATA / 'families.json'
# Genera renamed after the eBird 2021 / iNat 2024 snapshots (reviewed by hand).
GENUS_FAMILY = {
    'Anarhynchus': 'Charadriidae', 'Thinornis': 'Charadriidae', 'Eudromias': 'Charadriidae',
    'Astur': 'Accipitridae', 'Tachyspiza': 'Accipitridae', 'Coloeus': 'Corvidae', 'Botaurus': 'Ardeidae',
    'Nannopsittacus': 'Psittaculidae', 'Trachylaemus': 'Lybiidae',
}
FAMILY_JA = json.loads((Path(__file__).parent / 'family_ja.json').read_text(encoding='utf-8'))


def build(taxonomy_path):
    taxonomy = json.loads(Path(taxonomy_path).read_text(encoding='utf-8'))['mappings']
    pairs = lambda name: taxonomy[name]['mapped_pairs']
    inat = {key.lower(): value for key, value in pairs('inat2024_to_family').items()}
    ebird_family = {**pairs('ebird2021_species_to_family'), **pairs('ebird2022_species_to_family')}
    codes = json.loads((DATA / 'ebird-codes.json').read_text(encoding='utf-8'))
    names = sorted(json.loads((DATA / 'species.json').read_text(encoding='utf-8'))['species'])
    found, how = {}, Counter()
    for name in names:
        family = inat.get(name.lower())
        if family:
            how['inat2024'] += 1
        else:
            family = ebird_family.get(codes.get(name) or '')
            how['ebird' if family else 'none'] += bool(family) or 1
        if family:
            found[name] = family.capitalize()
    # A genus whose mapped species all share one family gives that family to its unmapped species.
    genus_families = defaultdict(Counter)
    for name, family in found.items():
        genus_families[name.split()[0]][family] += 1
    for name in names:
        genus = name.split()[0]
        if name in found or ' ' not in name:
            continue
        family = GENUS_FAMILY.get(genus)
        if family is None and len(genus_families.get(genus, ())) == 1:
            family = next(iter(genus_families[genus]))
        if family:
            found[name] = family
            how['genus'] += 1
            how['none'] -= 1
    japan = json.loads((DATA / 'japan-birds.json').read_text(encoding='utf-8'))['species']
    missing = [name for name in japan if name not in found]
    if missing:
        raise SystemExit(f'Japan checklist species without a family: {missing}')
    japan_families = sorted({found[name] for name in japan})
    unnamed = [family for family in japan_families if family not in FAMILY_JA]
    if unnamed:
        raise SystemExit(f'Japan checklist families without a Japanese name: {unnamed}')
    families = {family: ({'ja': FAMILY_JA[family]} if family in FAMILY_JA else {})
                for family in sorted(set(found.values()))}
    return {
        'sources': [{'name': 'perch-hoplite taxonomy database', 'license': 'Apache-2.0',
                     'file': 'perch_hoplite/taxonomy/taxonomy_database.json',
                     'sha256': hashlib.sha256(Path(taxonomy_path).read_bytes()).hexdigest()},
                    {'name': 'Checklist of Japanese Birds, 8th edition (family names)',
                     'publisher': 'Ornithological Society of Japan'}],
        'counts': {'taxa': len(names), 'with_family': len(found), 'families': len(families),
                   'japan_families': len(japan_families), **{f'by_{k}': v for k, v in how.items()}},
        'families': families, 'species': dict(sorted(found.items())),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('taxonomy')
    args = parser.parse_args()
    data = build(args.taxonomy)
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=0, sort_keys=False) + '\n', encoding='utf-8')
    print(data['counts'])
