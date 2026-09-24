"""Compare display choices on local audio; does not modify app data or defaults."""
import argparse
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
from PIL import Image, ImageDraw
import soundfile as sf

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'apps/sidecar'))
from report_figures import DATA, font

OUTPUT = REPO / 'output/spectrogram-study-20260916'
PALETTE = np.asarray(json.loads((DATA / 'roseus.json').read_text()), np.uint8).reshape(256, 3)
PLOT_W, PLOT_H = 1120, 240


def power(samples, rate, window_ms, hop_ms=2, padding=2):
    size, hop = round(rate * window_ms / 1000), round(rate * hop_ms / 1000)
    window = np.hanning(size + 1)[:-1]
    frames = np.lib.stride_tricks.sliding_window_view(np.pad(samples, (size // 2, size)), size)[::hop]
    frames = frames[:math.ceil(len(samples) / hop)]
    f = np.fft.rfftfreq(size * padding, 1 / rate)
    selected = f <= 12000
    values = np.concatenate([np.abs(np.fft.rfft(chunk * window, n=size * padding)[:, selected] / window.sum()) ** 2
                             for chunk in np.array_split(frames, max(1, math.ceil(len(frames) / 256)))])
    return f[selected], 10 * np.log10(np.maximum(values.T, 1e-12))


def color_image(frequencies, db, *, mel=False, ceiling=None, span=70):
    # Mel is a display projection, not mel-band averaging or a change to the audio.
    top = frequencies[-1]
    rows = np.linspace(0, 1, PLOT_H)
    target = 700 * (np.exp(rows * np.log1p(top / 700)) - 1) if mel else rows * top
    low = np.minimum(np.searchsorted(frequencies, target, side='right') - 1, len(frequencies) - 2)
    fraction = (target - frequencies[low]) / (frequencies[low + 1] - frequencies[low])
    projected = db[low] * (1 - fraction[:, None]) + db[low + 1] * fraction[:, None]
    ceiling = max(-80., float(db.max())) if ceiling is None else ceiling
    pixels = (np.clip((projected - ceiling + span) / span, 0, 1) * 255).astype(np.uint8)[::-1]
    return Image.fromarray(PALETTE[pixels]).resize((PLOT_W, PLOT_H), Image.Resampling.BILINEAR)


def sheet(name, title, subtitle, panels, start, duration, max_khz=12):
    image = Image.new('RGB', (1280, 145 + len(panels) * 330), '#faf8f4')
    draw = ImageDraw.Draw(image)
    draw.text((35, 22), title, font=font(27), fill='#292839')
    draw.text((35, 65), subtitle, font=font(17), fill='#575364')
    for index, (heading, picture, mel) in enumerate(panels):
        y = 108 + index * 330
        draw.text((35, y), heading, font=font(18), fill='#292839')
        image.paste(picture, (110, y + 36))
        for khz in range(0, max_khz + 1, 2):
            ratio = np.log1p(khz * 1000 / 700) / np.log1p(max_khz * 1000 / 700) if mel else khz / max_khz
            draw.text((97, y + 36 + (1 - ratio) * PLOT_H), f'{khz:g}', font=font(15), fill='#575364', anchor='rm')
        draw.text((35, y + 150), 'kHz', font=font(15), fill='#575364')
        for fraction in (0, .25, .5, .75, 1):
            draw.text((110 + fraction * PLOT_W, y + 291), f'{duration * fraction:.2f}', font=font(15), fill='#575364', anchor='mm')
    draw.text((35, image.height - 28), f'横軸：区間内の秒数（元録音 {start:.2f} 秒から）。Audacity本体の描画を再現した図ではありません。', font=font(15), fill='#575364')
    image.save(OUTPUT / name)


def read(path, start, end):
    info = sf.info(path)
    return sf.read(path, start=round(start * info.samplerate), stop=round(end * info.samplerate), dtype='float32')[0], info.samplerate


def main():
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', choices=['isolated', 'chorus', 'overlap'], default='isolated')
    example = parser.parse_args().example
    hour, start, end, a, b, max_khz = {
        'isolated': (15, 1680, 1695, 1686.8, 1689.0, 6),
        'chorus': (6, 330, 345, 333, 336, 8),
        'overlap': (8, 1170, 1185, 1179, 1182, 8),
    }[example]
    if example != 'isolated':
        OUTPUT = REPO / f'output/spectrogram-{example}-study-20260916'
    OUTPUT.mkdir(parents=True, exist_ok=True)
    source = Path(f'/Users/okamoto/Music/whombat_data/test/2MA04037_20240808_{hour:02d}0000.wav')
    samples, rate = read(source, start, end)
    sf.write(OUTPUT / 'excerpt.wav', samples, rate, subtype='PCM_16')
    # Keep the original baseline reproducible after the app's defaults change.
    _, original = power(samples, rate, 32, 8, 1)
    pixels = (np.clip((original - max(-80., float(original.max())) + 70) / 70, 0, 1) * 255).astype(np.uint8)[::-1]
    baseline = Image.fromarray(PALETTE[pixels]).resize((PLOT_W, PLOT_H), Image.Resampling.BILINEAR)
    f, db = power(samples, rate, 32, 4)
    sheet('display-comparison.png', '同じ15秒を、表示設定だけ変えて比較', '元音声・色テーマ・表示サイズは共通。見え方の検討用で、種の正解や検出精度の評価ではありません。', [
        ('A  変更前：32 ms窓／8 ms刻み・線形軸・区間ごとの明るさ', baseline, False),
        ('B  32 ms窓／4 ms刻み＋ゼロ埋め2倍・線形軸・同じ明るさ設定', color_image(f, db), False),
        ('C  Bの周波数軸をメル尺度へ変更', color_image(f, db, mel=True), True),
        ('D  Cの明るさを固定：Gain 20 dB／Range 80 dB相当', color_image(f, db, mel=True, ceiling=-20, span=80), True),
    ], start, end - start)
    samples2, rate2 = read(source, a, b)
    sf.write(OUTPUT / 'detail.wav', samples2, rate2, subtype='PCM_16')
    variants = [(16, *power(samples2, rate2, 16)), (32, *power(samples2, rate2, 32)), (2048 / rate2 * 1000, *power(samples2, rate2, 2048 / rate2 * 1000))]
    variants = [(ms, freq[freq <= max_khz * 1000], values[freq <= max_khz * 1000]) for ms, freq, values in variants]
    ceiling = max(float(values.max()) for _, _, values in variants)
    sheet('window-comparison.png', '窓の長さで変わる鳴き声の輪郭', '2 ms刻み・ゼロ埋め2倍・線形軸・明るさの基準を共通にして、窓の長さだけ変更。', [
        (f'{ms:.2f} ms窓（{round(rate2 * ms / 1000)}サンプル）', color_image(freq, values, ceiling=ceiling), False)
        for ms, freq, values in variants
    ], a, b - a, max_khz=max_khz)
    timings = {}
    for label, hop, padding in [('current', 8, 1), ('proposal', 4, 2), ('fine', 2, 2)]:
        elapsed = []
        for _ in range(4):
            started = time.perf_counter(); freq, values = power(samples, rate, 32, hop, padding)
            elapsed.append((time.perf_counter() - started) * 1000)
        timings[label] = {'fft_ms_median': float(np.median(elapsed[1:])), 'shape': list(values.shape)}
    (OUTPUT / 'measurements.json').write_text(json.dumps({'source': str(source), 'range': [start, end],
        'detail_range': [a, b], 'detail_max_khz': max_khz, 'rate': rate, 'timings': timings}, indent=2))
    other = 'overlap' if example == 'chorus' else 'chorus'
    other_link = f'<p><a href="../spectrogram-{other}-study-20260916/index.html">もう一つの録音で比べる</a></p>' if example != 'isolated' else ''
    (OUTPUT / 'index.html').write_text(f'''<!doctype html>
<html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>スペクトログラムの表示設定を比べる</title>
<style>
body {{ font: 18px/1.7 system-ui,sans-serif; color: #292839; background: #faf8f4; max-width: 1280px; margin: auto; padding: 24px; }}
h1 {{ font-size: 28px; }} h2 {{ font-size: 23px; margin-top: 36px; }}
a {{ color: #315e85; }} audio {{ display: block; width: min(100%,600px); margin: 16px 0; }}
img {{ display: block; width: 100%; height: auto; }} .source {{ overflow-wrap: anywhere; font-size: 15px; }}
</style>
<h1>スペクトログラムの表示設定を比べる</h1>
<p>同じ音に、前回と同じ表示設定を適用しました。音の混合・音量調整・ノイズ除去はしていません。</p>
<p class="source">元録音：{source.name} ／ {start}–{end} 秒 ／ {rate:,} Hz<br>
種数や種名の確認には使用せず、表示の見やすさを比較します。アプリの設定は変更していません。</p>
{other_link}
<p><a href="#display">15秒全体</a> · <a href="#window">重なる部分を拡大</a></p>
<h2 id="display">15秒全体：同じ音を聴きながら、A〜Dを比較</h2>
<audio controls preload="metadata" src="excerpt.wav" aria-label="比較する15秒の音声"></audio>
<a href="display-comparison.png"><img src="display-comparison.png" alt="現行、描画密度を増やす案、メル軸、固定ゲインの4設定を比較"></a>
<h2 id="window">その中の {a-start:g}〜{b-start:g} 秒：窓の長さを比較</h2>
<audio controls preload="metadata" src="detail.wav" aria-label="拡大部分の音声"></audio>
<a href="window-comparison.png"><img src="window-comparison.png" alt="16、32、85.33ミリ秒の窓長を同じ明るさ基準で比較"></a>
<p>図の横軸は、それぞれの切り出し区間の先頭を0秒にしています。図を押すと原寸画像を開きます。Audacity本体の描画を再現した図ではありません。</p>
</html>''', encoding='utf-8')
    print(json.dumps(timings, indent=2))


if __name__ == '__main__':
    main()
