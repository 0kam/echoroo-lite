"""Build the Perch candidate list from the OSJ Checklist of Japanese Birds, 8th edition."""
import argparse
import csv
import hashlib
import json
import shutil
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_URL = "https://ornithology.jp/materials/checklist/jpbirdlist8ed_ver1.xlsx"
WIKIDATA_URL = "https://query.wikidata.org/sparql"
OUTPUT = ROOT / "apps/sidecar/data/japan-birds.json"
DEFAULT_BIRDNET_LABELS = ROOT / "poc/models/birdnet/acoustic-models/v2.4/tf/labels/en_us.txt"
# Reviewed genus moves in Perch's iNaturalist 2024 taxonomy (same species epithet).
MANUAL = {
    "Accipiter gentilis": "Astur gentilis",
    "Accipiter gularis": "Tachyspiza gularis",
    "Accipiter soloensis": "Tachyspiza soloensis",
    "Bubulcus ibis": "Ardea ibis",
    "Charadrius alexandrinus": "Anarhynchus alexandrinus",
    "Charadrius dubius": "Thinornis dubius",
    "Charadrius placidus": "Thinornis placidus",
    "Charadrius veredus": "Anarhynchus veredus",
    "Corvus dauuricus": "Coloeus dauuricus",
    "Corvus monedula": "Coloeus monedula",
    "Ixobrychus cinnamomeus": "Botaurus cinnamomeus",
    "Ixobrychus eurhythmus": "Botaurus eurhythmus",
    "Ixobrychus flavicollis": "Botaurus flavicollis",
    "Ixobrychus sinensis": "Botaurus sinensis",
    "Pardaliparus venustulus": "Periparus venustulus",
}
# Reviewed BirdNET 2.4 names, keyed by the app's canonical Perch name.
BIRDNET_MANUAL = {
    "Anarhynchus alexandrinus": "Charadrius alexandrinus",
    "Anarhynchus leschenaultii": "Charadrius leschenaultii",
    "Anarhynchus mongolus": "Charadrius mongolus",
    "Anarhynchus veredus": "Charadrius veredus",
    "Ardea ibis": "Bubulcus ibis",
    "Astur gentilis": "Accipiter gentilis",
    "Botaurus cinnamomeus": "Ixobrychus cinnamomeus",
    "Botaurus eurhythmus": "Ixobrychus eurhythmus",
    "Botaurus flavicollis": "Ixobrychus flavicollis",
    "Botaurus sinensis": "Ixobrychus sinensis",
    "Coloeus dauuricus": "Corvus dauuricus",
    "Coloeus monedula": "Corvus monedula",
    "Emberiza personata": "Emberiza spodocephala",
    "Eudromias morinellus": "Charadrius morinellus",
    "Gallirallus okinawae": "Gallirallus okinawae",
    "Helopsaltes amnicola": "Locustella amnicola",
    "Helopsaltes certhiola": "Locustella certhiola",
    "Helopsaltes ochotensis": "Locustella ochotensis",
    "Helopsaltes pleskei": "Locustella pleskei",
    "Helopsaltes pryeri": "Locustella pryeri",
    "Larus vegae": "Larus argentatus",
    "Parus cinereus": "Parus minor",
    "Phasianus versicolor": "Phasianus colchicus",
    "Tachyspiza gularis": "Accipiter gularis",
    "Tachyspiza soloensis": "Accipiter soloensis",
    "Thinornis dubius": "Charadrius dubius",
    "Thinornis placidus": "Charadrius placidus",
}
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def download_checklist():
    path = ROOT / ".local-data/jpbirdlist8ed_ver1.xlsx"
    path.parent.mkdir(exist_ok=True)
    if not path.is_file():
        with urllib.request.urlopen(CHECKLIST_URL, timeout=60) as source, path.open("wb") as target:
            shutil.copyfileobj(source, target)
    return path


def xlsx_rows(path):
    with ZipFile(path) as archive:
        shared = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
        strings = ["".join(node.text or "" for node in item.findall(".//m:t", NS))
                   for item in shared.findall("m:si", NS)]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet2.xml"))
    for row in sheet.findall(".//m:sheetData/m:row", NS):
        values = {}
        for cell in row.findall("m:c", NS):
            value = cell.find("m:v", NS)
            text = "" if value is None else value.text or ""
            if cell.get("t") == "s" and text:
                text = strings[int(text)]
            values[cell.get("r")[0]] = text
        yield values


def checklist_species(path):
    return [{"scientific_name": row["F"], "ja": row["H"], "part": row["B"]}
            for row in xlsx_rows(path)
            if row.get("B") in {"A", "B"} and row.get("C") == "種シュ"]


def perch_species(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = csv.reader(stream)
        if next(rows) != ["inat2024_fsd50k"]:
            raise ValueError("Unexpected Perch labels header")
        return {row[0] for row in rows if " " in row[0] and "_" not in row[0]}


def birdnet_species(path):
    return {line.partition("_")[0] for line in path.read_text(encoding="utf-8").splitlines() if "_" in line}


def add_birdnet_mappings(payload, labels_path):
    labels = birdnet_species(labels_path)
    rows_by_species = {}
    for row in payload["mappings"]:
        rows_by_species.setdefault(row["perch_scientific_name"], []).append(row)
    resolved = {canonical: target for canonical, target in BIRDNET_MANUAL.items()
                if canonical in rows_by_species and target in labels}
    methods = {canonical: "manual" for canonical in resolved}
    claimed = {target: canonical for canonical, target in resolved.items()}
    if len(claimed) != len(resolved):
        raise ValueError("Two manual mappings use the same BirdNET species")
    for canonical, rows in rows_by_species.items():
        if canonical in resolved:
            continue
        checklist = {row["scientific_name"] for row in rows if row["scientific_name"] in labels}
        if len(checklist) > 1:
            raise ValueError(f"Conflicting checklist names for {canonical}: {sorted(checklist)}")
        matched = next(iter(checklist), "")
        method = "checklist_exact"
        if not matched and canonical in labels:
            matched, method = canonical, "perch_exact"
        if matched in claimed:
            matched, method = "", "unmatched"
        if matched:
            claimed[matched] = canonical
        resolved[canonical], methods[canonical] = matched, method if matched else "unmatched"
    for row in payload["mappings"]:
        canonical = row["perch_scientific_name"]
        matched, method = resolved[canonical], methods[canonical]
        row["birdnet_scientific_name"] = matched
        row["birdnet_method"] = method
    mapped = [name for name in resolved.values() if name]
    if len(mapped) != len(set(mapped)):
        raise ValueError("Two app species map to the same BirdNET species")
    payload["stats"].update({
        f"birdnet_{method}": sum(value == method for value in methods.values())
        for method in ("manual", "checklist_exact", "perch_exact", "unmatched")
    })
    payload["sources"] = [source for source in payload["sources"] if source.get("name") != "BirdNET 2.4 English labels"]
    payload["sources"].append({"name": "BirdNET 2.4 English labels", "file": labels_path.name,
                               "sha256": digest(labels_path)})
    return payload


def wikidata_matches(names, perch):
    """Return accepted taxon names linked through Wikidata's taxon-synonym relation."""
    found = {}
    for start in range(0, len(names), 100):
        values = " ".join(json.dumps(name) for name in names[start:start + 100])
        query = f"""
SELECT DISTINCT ?check ?acceptedName WHERE {{
  VALUES ?check {{ {values} }}
  ?named wdt:P225 ?check.
  {{ ?accepted wdt:P1420 ?named. }} UNION {{ ?named wdt:P1420 ?accepted. }}
  ?accepted wdt:P225 ?acceptedName.
}}"""
        body = urllib.parse.urlencode({"query": query, "format": "json"}).encode()
        request = urllib.request.Request(WIKIDATA_URL, data=body, headers={
            "User-Agent": "Echoroo-Lite/0.1 species-data builder",
            "Content-Type": "application/x-www-form-urlencoded",
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            bindings = json.load(response)["results"]["bindings"]
        for row in bindings:
            accepted = row["acceptedName"]["value"]
            if accepted in perch:
                found.setdefault(row["check"]["value"], set()).add(accepted)
    ambiguous = {name: sorted(values) for name, values in found.items() if len(values) > 1}
    if ambiguous:
        raise ValueError(f"Ambiguous Wikidata mappings; add reviewed manual mappings: {ambiguous}")
    return {name: next(iter(values)) for name, values in found.items()}


def build(checklist_path, labels_path, output=OUTPUT, offline=False, birdnet_labels=DEFAULT_BIRDNET_LABELS):
    checklist = checklist_species(checklist_path)
    perch = perch_species(labels_path)
    exact = {row["scientific_name"] for row in checklist} & perch
    remaining = [row["scientific_name"] for row in checklist if row["scientific_name"] not in exact]
    aliases = {} if offline else wikidata_matches(remaining, perch)
    mappings, unmatched = [], []
    for row in checklist:
        original = row["scientific_name"]
        if original in exact:
            matched, method = original, "exact"
        elif original in aliases:
            matched, method = aliases[original], "wikidata"
        elif MANUAL.get(original) in perch:
            matched, method = MANUAL[original], "manual"
        else:
            unmatched.append(row)
            continue
        mappings.append({**row, "perch_scientific_name": matched, "method": method})
    stats = {"checklist_species": len(checklist), **{f"matched_{method}": sum(r["method"] == method for r in mappings)
             for method in ("exact", "wikidata", "manual")}, "unmatched": len(unmatched)}
    sources = [
            {"name": "Checklist of Japanese Birds, 8th edition", "publisher": "Ornithological Society of Japan",
             "version": "8", "url": CHECKLIST_URL, "file": checklist_path.name, "sha256": digest(checklist_path)},
            {"name": "Perch V2 labels", "file": labels_path.name, "sha256": digest(labels_path)},
    ]
    if not offline:
        sources.append({"name": "Wikidata", "url": WIKIDATA_URL, "license": "CC0",
                        "retrieved": date.today().isoformat(), "mappings": stats["matched_wikidata"]})
    payload = {
        "sources": sources,
        "stats": stats,
        "species": sorted({row["perch_scientific_name"] for row in mappings}),
        "mappings": mappings,
        "unmatched": unmatched,
    }
    add_birdnet_mappings(payload, birdnet_labels)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(stats, ensure_ascii=False))
    print("Unmatched:", ", ".join(row["scientific_name"] for row in unmatched) or "none")
    return payload


def add_birdnet_to_existing(output=OUTPUT, labels_path=DEFAULT_BIRDNET_LABELS):
    payload = json.loads(output.read_text(encoding="utf-8"))
    add_birdnet_mappings(payload, labels_path)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], ensure_ascii=False))
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("xlsx", nargs="?", type=Path, help="Downloaded jpbirdlist8ed_ver1.xlsx")
    parser.add_argument("--labels", type=Path, default=ROOT / "poc/models/perch-labels/assets/labels.csv")
    parser.add_argument("--birdnet-labels", type=Path, default=DEFAULT_BIRDNET_LABELS)
    parser.add_argument("--birdnet-only", action="store_true", help="Add BirdNET mappings to the existing JSON without rebuilding Perch mappings")
    parser.add_argument("--offline", action="store_true", help="Skip Wikidata and leave those taxa unmatched")
    args = parser.parse_args()
    if args.birdnet_only:
        add_birdnet_to_existing(labels_path=args.birdnet_labels.resolve())
    else:
        build((args.xlsx or download_checklist()).resolve(), args.labels.resolve(), offline=args.offline,
              birdnet_labels=args.birdnet_labels.resolve())
