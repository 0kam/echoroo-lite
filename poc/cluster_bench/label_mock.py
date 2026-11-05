"""Bake mock v3 (pick sounds on the sound map, label them, export): real groups, sounds and spectrograms from a running sidecar.

Usage: python label_mock.py <sidecar url> <token> <dataset id> <group name, e.g. 1-1-4> [open child name]
"""
import base64, io, json, re, struct, sys, urllib.parse, urllib.request
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
URL, TOKEN, DATASET, GROUP = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
OPEN = sys.argv[5] if len(sys.argv) > 5 else None
LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']

def get(path, raw=False):
    request = urllib.request.Request(URL + path, headers={'Authorization': f'Bearer {TOKEN}'})
    with urllib.request.urlopen(request) as response:
        body = response.read()
    return body if raw else json.loads(body)

palette = [int(v) for v in re.findall(r'\d+', (ROOT / 'apps/web/src/lib/roseus.ts').read_text().split('new Uint8Array([', 1)[1].split('])', 1)[0])]
bundled = json.loads((ROOT / 'apps/sidecar/data/species.json').read_text())['species']
def ja(scientific):
    record = bundled.get(scientific) or {}
    names = record.get('common_names') or record.get('names') or record
    return (names.get('ja') if isinstance(names, dict) else None) or scientific

def png(image):
    buffer = io.BytesIO(); image.save(buffer, 'PNG', optimize=True)
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode()

def jpeg(image):
    buffer = io.BytesIO(); image.resize((600, 160)).save(buffer, 'JPEG', quality=80)
    return 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode()

def spectrogram(segment):
    query = urllib.parse.urlencode({'start_s': segment['start_s'], 'end_s': segment['end_s'], 'scale': 'mel'})
    data = get(f"/sources/recording/{segment['recording_id']}/spectrogram?{query}")
    levels = base64.b64decode(data['pixels'])
    image = Image.frombytes('L', (data['width'], data['height']), levels).convert('P')
    image.putpalette(palette)
    return jpeg(image.convert('RGB')), [round(t['frequency_hz'] / 1000, 1) for t in data['frequency_ticks']], [t['row_fraction'] for t in data['frequency_ticks']]

# Find the group by name along the tree.
node = get(f'/explore?dataset={DATASET}&node=0')
for depth in range(1, GROUP.count('-') + 2):
    name = '-'.join(GROUP.split('-')[:depth])
    child = next(c for c in node['children'] if c['name'] == name)
    node = get(f"/explore?dataset={DATASET}&node={child['node']}")

def group(g):
    return {'node': g['node'], 'name': g['name'], 'size': g['size'], 'children': g['child_count'], 'hint': g['hint_rate'],
            'top': [{'name': ja(s['scientific_name']), 'scientific': s['scientific_name'], 'share': s['share']} for s in g['top_species']]}

rows = [group(c) for c in node['children']]
opened = next(c for c in node['children'] if c['name'] == (OPEN or node['children'][0]['name']))
sounds = []
for s in get(f"/explore/nodes/{opened['node']}/segments?dataset={DATASET}")['segments'][:8]:
    image, ticks, fractions = spectrogram(s)
    sounds.append({'id': s['segment_id'], 'recorded_at': s['recorded_at'], 'start_s': s['start_s'], 'end_s': s['end_s'], 'timezone': s['timezone'],
                   'file': Path(s['path']).name, 'candidates': [{'name': ja(c['scientific_name']), 'scientific': c['scientific_name'], 'score': c['score']} for c in s['candidates']],
                   'image': image, 'ticks': ticks, 'fractions': fractions})

# The map at this level: every point faint, the group's children in colour.
buffer = get(f'/explore/points?dataset={DATASET}', raw=True)
n = struct.unpack_from('<I', buffer)[0]
xs = struct.unpack_from(f'<{n}f', buffer, 4); ys = struct.unpack_from(f'<{n}f', buffer, 4 + 4 * n)
lo, hi = node['node']['lo'], node['node']['hi']
# Fit the middle 96% of the group's points, so a few stray points do not shrink the picture.
qx, qy = sorted(xs[lo:hi]), sorted(ys[lo:hi]); cut = (hi - lo) // 50
box = (qx[cut], qx[-1 - cut], qy[cut], qy[-1 - cut])
W, H, pad = 920, 560, 40
sx = (W - 2 * pad) / (box[1] - box[0]); sy = (H - 2 * pad) / (box[3] - box[2]); k = min(sx, sy)
ox = (W - k * (box[1] - box[0])) / 2; oy = (H - k * (box[3] - box[2])) / 2
canvas = Image.new('RGB', (W, H), (255, 250, 243)); draw = ImageDraw.Draw(canvas)
at = lambda i: (ox + (xs[i] - box[0]) * k, H - (oy + (ys[i] - box[2]) * k))
for i in range(n):
    if not lo <= i < hi:
        x, y = at(i)
        if 0 <= x < W and 0 <= y < H: draw.rectangle([x - 1, y - 1, x + 1, y + 1], fill=(228, 222, 220))
marks = []
for index, c in enumerate(node['children']):
    colour = LIGHT[index % len(LIGHT)]
    for i in range(c['lo'], c['hi']):
        x, y = at(i); draw.rectangle([x - 2, y - 2, x + 2, y + 2], fill=colour)
    middle = sorted(range(c['lo'], c['hi']), key=lambda i: xs[i])[(c['hi'] - c['lo']) // 2]
    marks.append(at(middle))
for index, (x, y) in enumerate(marks):
    draw.ellipse([x - 20, y - 20, x + 20, y + 20], fill=LIGHT[index % len(LIGHT)], outline='white', width=3)
    draw.text((x, y), str(index + 1), fill='white', anchor='mm', font_size=24)

species = sorted({ja(name) for name in json.loads((ROOT / 'apps/sidecar/data/japan-birds.json').read_text()).get('species', [])} - {None})
data = {'trail': [p['name'] for p in node['path']], 'node': group(node['node']), 'rows': rows, 'opened': opened['node'], 'sounds': sounds,
        'map': png(canvas), 'species': species}
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
out = ROOT / 'docs/prototypes/explore-label-mock.html'
out.write_text((Path(__file__).parent / 'label_mock_template.html').read_text().replace('/*DATA*/', payload))
print('written', out, f'{out.stat().st_size / 1e3:.0f} kB', len(rows), 'rows', len(sounds), 'sounds', len(data['species']), 'species')
