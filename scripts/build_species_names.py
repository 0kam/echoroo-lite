"""Build offline Japanese and English model-taxon names from OSJ and Wikidata."""
import argparse
import csv
import json
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from build_japan_species import (CHECKLIST_URL, ROOT, WIKIDATA_URL, checklist_species,
                                 digest, download_checklist)

OUTPUT = ROOT / "apps/sidecar/data/species.json"
NON_SPECIES = {"Dog", "Engine", "Environmental", "Fireworks", "Gun", "Noise", "Siren",
               "Human non-vocal", "Human vocal", "Human whistle"}


def model_species(perch_labels, birdnet_labels):
    with perch_labels.open(newline="", encoding="utf-8") as stream:
        rows = csv.reader(stream)
        if next(rows) != ["inat2024_fsd50k"]:
            raise ValueError("Unexpected Perch labels header")
        names = {row[0] for row in rows if " " in row[0] and "_" not in row[0]}
    if birdnet_labels and birdnet_labels.is_file():
        names.update(scientific for row in birdnet_labels.read_text(encoding="utf-8").splitlines()
                     if "_" in row and (scientific := row.partition("_")[0]) not in NON_SPECIES)
    return sorted(names)


def wikidata_names(names):
    found = {}
    for start in range(0, len(names), 100):
        values = " ".join(json.dumps(name) for name in names[start:start + 100])
        query = f"""
SELECT ?name ?itemLabel ?jaLabel ?enCommon WHERE {{
  VALUES ?name {{ {values} }}
  ?item wdt:P225 ?name.
  OPTIONAL {{ ?item rdfs:label ?jaLabel. FILTER(LANG(?jaLabel) = "ja") }}
  OPTIONAL {{ ?item wdt:P1843 ?enCommon. FILTER(LANG(?enCommon) = "en") }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". }}
}}"""
        body = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
        request = urllib.request.Request(WIKIDATA_URL, data=body, headers={
            "User-Agent": "Echoroo-Lite/0.1 species-data builder",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)["results"]["bindings"]
        # Taxon English labels are often the scientific name; prefer a sorted P1843 common name.
        rows.sort(key=lambda row: row.get("enCommon", {}).get("value") or "\uffff")
        for row in rows:
            item = found.setdefault(row["name"]["value"], {})
            if label := row.get("enCommon", {}).get("value"):
                item.setdefault("en", label[0].upper() + label[1:])
            if (label := row.get("itemLabel", {}).get("value")) and label != row["name"]["value"] and not label.startswith("http"):
                item.setdefault("en", label)
            if label := row.get("jaLabel", {}).get("value"):
                item.setdefault("ja", label)
    return found


def build(checklist_path, perch_labels, birdnet_labels=None,
          japan_species=ROOT / "apps/sidecar/data/japan-birds.json", output=OUTPUT):
    taxa = model_species(perch_labels, birdnet_labels)
    mappings = json.loads(japan_species.read_text(encoding="utf-8"))["mappings"] if japan_species.is_file() else []
    # Renamed Wikidata items often lack names; fall back to the checklist's name.
    renamed = {row["perch_scientific_name"]: row["scientific_name"] for row in mappings if row["method"] != "exact"}
    names = wikidata_names(list(taxa) + sorted(set(renamed.values()) - set(taxa)))
    for perch, original in renamed.items():
        if "en" not in names.get(perch, {}) and (english := names.get(original, {}).get("en")):
            names.setdefault(perch, {})["en"] = english
    wikidata_coverage = {lang: sum(bool(names.get(name, {}).get(lang)) for name in taxa) for lang in ("ja", "en")}
    osj = {row["scientific_name"]: row["ja"] for row in checklist_species(checklist_path)}
    for row in mappings:
        osj[row["perch_scientific_name"]] = row["ja"]
    for scientific, ja in osj.items():
        if scientific in taxa:
            names.setdefault(scientific, {})["ja"] = ja
    species = {name: names.get(name, {}) for name in taxa}
    coverage = {lang: sum(bool(value.get(lang)) for value in species.values()) for lang in ("ja", "en")}
    payload = {
        "sources": [
            {"name": "Checklist of Japanese Birds, 8th edition", "publisher": "Ornithological Society of Japan",
             "version": "8", "url": CHECKLIST_URL, "file": checklist_path.name, "sha256": digest(checklist_path),
             "japanese_names": sum(name in osj for name in taxa)},
            {"name": "Wikidata", "url": WIKIDATA_URL, "license": "CC0", "retrieved": date.today().isoformat(),
             "names": wikidata_coverage},
            {"name": "Perch V2 labels", "file": perch_labels.name, "sha256": digest(perch_labels),
             "combined_model_taxa": len(taxa)},
        ],
        "counts": {"taxa": len(species), **coverage},
        "species": species,
    }
    if birdnet_labels and birdnet_labels.is_file():
        payload["sources"].append({"name": "BirdNET 2.4 labels (scientific names only)",
                                   "file": birdnet_labels.name, "sha256": digest(birdnet_labels)})
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["counts"], ensure_ascii=False))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", nargs="?", type=Path, help="Downloaded jpbirdlist8ed_ver1.xlsx")
    parser.add_argument("--perch-labels", type=Path, default=ROOT / "poc/models/perch-labels/assets/labels.csv")
    parser.add_argument("--birdnet-labels", type=Path,
                        default=ROOT / "poc/models/birdnet/acoustic-models/v2.4/tf/labels/en_us.txt")
    parser.add_argument("--japan-species", type=Path, default=ROOT / "apps/sidecar/data/japan-birds.json")
    args = parser.parse_args()
    build((args.xlsx or download_checklist()).resolve(), args.perch_labels.resolve(), args.birdnet_labels.resolve(),
          args.japan_species.resolve())
