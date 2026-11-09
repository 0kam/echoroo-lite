"""Bake mock v5 of the sound map (find a sound on the map, collect nearby sounds, export) from a running sidecar.

Usage: python flow_mock.py <sidecar url> <token> <dataset id> <group name, e.g. 1> [open child name] [output html]
The current sound is the open child's point nearest its badge; its row lists sounds closest to it (`near=`).
Three of them are ticked: the current sound itself and the third one with two different species, the fourth one blank.
"""
import base64, io, json, re, struct, sys, urllib.parse, urllib.request
from pathlib import Path
from statistics import median
from PIL import Image, ImageColor, ImageDraw

ROOT = Path(__file__).resolve().parents[2]
URL, TOKEN, DATASET, GROUP = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
OPEN = sys.argv[5] if len(sys.argv) > 5 else None
OUT = Path(sys.argv[6]) if len(sys.argv) > 6 else ROOT / 'docs/prototypes/explore-flow-mock.html'
LIGHT = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300', '#6250d6', '#e34948']
SHOWN = 8  # sounds baked into the open row; the app shows 20 per page

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

def spectrogram(segment):
    query = urllib.parse.urlencode({'start_s': segment['start_s'], 'end_s': segment['end_s'], 'scale': 'mel'})
    data = get(f"/sources/recording/{segment['recording_id']}/spectrogram?{query}")
    image = Image.frombytes('L', (data['width'], data['height']), base64.b64decode(data['pixels'])).convert('P')
    image.putpalette(palette)
    buffer = io.BytesIO(); image.convert('RGB').resize((600, 200)).save(buffer, 'JPEG', quality=70)
    return {'image': 'data:image/jpeg;base64,' + base64.b64encode(buffer.getvalue()).decode(),
            'ticks': [[round(t['frequency_hz'] / 1000, 1), t['row_fraction']] for t in data['frequency_ticks']]}

def sound(s):
    return {'id': s['segment_id'], 'recorded_at': s['recorded_at'], 'start_s': s['start_s'], 'timezone': s['timezone'],
            'candidates': [{'name': ja(c['scientific_name']), 'score': c['score']} for c in s['candidates']], **spectrogram(s)}

def group(g):
    return {'node': g['node'], 'name': g['name'], 'size': g['size'], 'children': g['child_count'], 'hint': g['hint_rate'],
            'top': [{'name': ja(s['scientific_name']), 'share': s['share']} for s in g['top_species']]}

# Find the group by name along the tree.
status = get(f'/explore?dataset={DATASET}&node=0')
for depth in range(1, GROUP.count('-') + 2):
    name = '-'.join(GROUP.split('-')[:depth])
    child = next(c for c in status['children'] if c['name'] == name)
    status = get(f"/explore?dataset={DATASET}&node={child['node']}")
node, children = status['node'], status['children']
selected = next(i for i, c in enumerate(children) if c['name'] == (OPEN or children[0]['name']))
opened = children[selected]

buffer = get(f'/explore/points?dataset={DATASET}', raw=True)
n = struct.unpack_from('<I', buffer)[0]
xs = struct.unpack_from(f'<{n}f', buffer, 4); ys = struct.unpack_from(f'<{n}f', buffer, 4 + 4 * n)
marks = [(median(xs[c['lo']:c['hi']]), median(ys[c['lo']:c['hi']])) for c in children]
# The current sound: the open child's point nearest its badge, as a click next to the badge would pick.
mx, my = marks[selected]
current = min(range(opened['lo'], opened['hi']), key=lambda i: (xs[i] - mx) ** 2 + (ys[i] - my) ** 2)
near = get(f"/explore/nodes/{opened['node']}/segments?dataset={DATASET}&near={current}")['segments'][:SHOWN]
sounds = [sound(s) for s in near]
card = get(f'/explore/points/{current}?dataset={DATASET}')
assert card['segment_id'] == sounds[0]['id'], 'the nearest sound to the current sound is itself'

# The map as the app draws it at 2x: the group's box fills 85% of the canvas, other points faint,
# the chosen child strong and its siblings faint, numbered badges at the children's medians, a ring on the current sound.
W, H = 880, 520
left, right = min(xs[node['lo']:node['hi']]), max(xs[node['lo']:node['hi']])
bottom, top = min(ys[node['lo']:node['hi']]), max(ys[node['lo']:node['hi']])
k = .85 * min(W / (right - left), H / (top - bottom))
at = lambda x, y: (W / 2 + (x - (left + right) / 2) * k, H / 2 - (y - (bottom + top) / 2) * k)
canvas = Image.new('RGB', (W, H), (255, 250, 243)); draw = ImageDraw.Draw(canvas, 'RGBA')
def dots(lo, hi, colour, alpha, size):
    fill = (*ImageColor.getrgb(colour), round(alpha * 255))
    for i in range(lo, hi):
        x, y = at(xs[i], ys[i])
        if -size < x < W + size and -size < y < H + size: draw.rectangle([x - size / 2, y - size / 2, x + size / 2, y + size / 2], fill=fill)
dots(0, node['lo'], '#373450', .1, 4); dots(node['hi'], n, '#373450', .1, 4)
for index, c in enumerate(children):
    if index != selected: dots(c['lo'], c['hi'], LIGHT[index % len(LIGHT)], .15, 6)
dots(opened['lo'], opened['hi'], LIGHT[selected % len(LIGHT)], .9, 6)
for index, mark in enumerate(marks):
    x, y = at(*mark)
    draw.ellipse([x - 22, y - 22, x + 22, y + 22], fill=LIGHT[index % len(LIGHT)], outline='white', width=4)
    draw.text((x, y), str(index + 1), fill='white', anchor='mm', font_size=24)
x, y = at(xs[current], ys[current]); draw.ellipse([x - 14, y - 14, x + 14, y + 14], outline='#373450', width=5)
map_buffer = io.BytesIO(); canvas.quantize(64).save(map_buffer, 'PNG', optimize=True)

# Ticked, oldest first: the current sound and the third sound with two different species (AI candidates first, then the
# row's and the group's top AI species), then the fourth sound blank, so "前に入れた種" has two species to offer.
pool = list(dict.fromkeys(s['name'] for s in sounds[0]['candidates'] + group(opened)['top'] + group(node)['top']))
picks = [{'id': sounds[0]['id'], 'species': pool[0], 'type': '', 'quality': 'B'},
         {'id': sounds[2]['id'], 'species': pool[1 % len(pool)], 'type': '', 'quality': 'C'},
         {'id': sounds[3]['id'], 'species': '', 'type': '', 'quality': ''}]
models = {m['id']: m['name'] for m in get('/models')['models']}
names = sorted({ja(s) for s in json.loads((ROOT / 'apps/sidecar/data/japan-birds.json').read_text())['species']})
data = {'created_at': status['created_at'], 'model': models.get(status['engine'], status['engine']), 'total': status['segment_count'],
        'trail': [p['name'] for p in status['path']], 'node': group(node), 'clip_s': node['rep']['end_s'] - node['rep']['start_s'],
        'rows': [group(c) for c in children], 'selected': selected, 'sounds': sounds, 'current': sounds[0]['id'], 'picks': picks, 'species': names,
        'map': 'data:image/png;base64,' + base64.b64encode(map_buffer.getvalue()).decode()}
payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
OUT.write_text((Path(__file__).parent / 'flow_mock_template.html').read_text().replace('/*DATA*/', payload))
print('written', OUT, f'{OUT.stat().st_size / 1e3:.0f} kB', f'map {len(map_buffer.getvalue()) / 1e3:.0f} kB', len(sounds), 'sounds', 'current position', current)
