"""Build the offline display-name seed from the already downloaded model labels."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
labels = ROOT / "poc/models/birdnet/acoustic-models/v2.4/tf/labels"
species = {}
sources = []
non_species = {"Dog", "Engine", "Environmental", "Fireworks", "Gun", "Noise", "Siren",
               "Human non-vocal", "Human vocal", "Human whistle"}
for locale, filename in (("en", "en_us.txt"), ("ja", "ja.txt")):
    path = labels / filename
    sources.append({"model": "BirdNET 2.4", "file": filename, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    for row in path.read_text(encoding="utf-8").splitlines():
        scientific, separator, common = row.partition("_")
        if not separator:
            raise ValueError(f"Malformed BirdNET label: {row}")
        if scientific not in non_species:
            species.setdefault(scientific, {})[locale] = common

path = ROOT / "poc/models/perch-labels/assets/labels.csv"
sources.append({"model": "Perch V2", "file": "assets/labels.csv", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
with path.open(newline="", encoding="utf-8") as stream:
    rows = csv.reader(stream)
    assert next(rows) == ["inat2024_fsd50k"]
    for row in rows:
        # This pinned export uses spaces for iNaturalist taxa, underscores for FSD sound classes.
        # Keep the two quoted Pelophylax hybrid names as supplied by the model.
        if " " in row[0] and "_" not in row[0]:
            species.setdefault(row[0], {})

output = ROOT / "apps/sidecar/data/species.json"
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps({"sources": sources, "species": dict(sorted(species.items()))},
                            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {len(species)} model taxa to {output}")
