"""Copy the user-selected koe-e-lab sample audio/photos, preserving their bytes."""
import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent / 'koe-e-lab/sample_data'
destination = ROOT / 'apps/sidecar/data/quiz'
stems = 'aogera gabicho hakusekirei hashibosogarasu hashibutogarasu hibari hiyodori hoojiro hototogisu kakko kawarahiwa kibitaki kiji kijibato kisekirei kogera mejiro mukudori ooyoshikiri segurosekirei shijukara suzume tsubame uguisu yabusame yamagara'.split()
japanese = 'アオゲラ ガビチョウ ハクセキレイ ハシボソガラス ハシブトガラス ヒバリ ヒヨドリ ホオジロ ホトトギス カッコウ カワラヒワ キビタキ キジ キジバト キセキレイ コゲラ メジロ ムクドリ オオヨシキリ セグロセキレイ シジュウカラ スズメ ツバメ ウグイス ヤブサメ ヤマガラ'.split()
catalog = json.loads((ROOT / 'apps/sidecar/data/species.json').read_text())['species']
scientific = {values.get('ja'): name for name, values in catalog.items() if values.get('ja')}
scientific['キジ'] = 'Phasianus versicolor'
names = dict(zip(stems, japanese))
items = []
with (ROOT / 'docs/quiz-source-inventory.csv').open() as stream:
    for row in csv.DictReader(stream):
        stem = row['source_stem']
        assert hashlib.sha256((source / row['audio_file']).read_bytes()).hexdigest() == row['audio_sha256']
        hashes = {}
        for field in ('audio_file', 'image_file'):
            target = destination / row[field]
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / row[field], target)
            hashes[field] = hashlib.sha256(target.read_bytes()).hexdigest()
        credit = {'recordist': '', 'source_url': '', 'license': '', 'photographer': '', 'image_license': ''}
        if stem == 'mukudori':
            # These two fields are present in the supplied MP3's ID3 tags; the license is not.
            credit.update(recordist='Anon Torimi', source_url='https://xeno-canto.org/510632')
        items.append({'key': f'koe-e-lab-v1:{stem}', 'scientific_name': scientific[names[stem]],
            'audio_file': row['audio_file'], 'image_file': row['image_file'], 'sha256': hashes,
            'sound_type': row['sound_type'], 'label_source': 'provided_filename', 'credit': credit})
assert len(items) == 26 and len({i['scientific_name'] for i in items}) == 26
(destination / 'manifest.json').write_text(json.dumps({'version': 1, 'source': 'koe-e-lab/sample_data',
    'purpose': 'practice', 'items': items}, ensure_ascii=False, indent=2) + '\n')
print(f'Bundled {len(items)} original audio files and photos: {destination}')
