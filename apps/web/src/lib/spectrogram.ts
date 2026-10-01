import { roseus } from './roseus';
/** Frequency ticks from the sidecar; `row_fraction` is 0 at the bottom and 1 at the top, for mel and linear alike. */
export type Tick = { frequency_hz: number; row_fraction: number };
export type Spectrogram = { width: number; height: number; pixels: string; frequency_ticks: Tick[] };
export const kHz = (tick: Tick) => +(tick.frequency_hz / 1000).toFixed(1);
/** Draws the sidecar's 8-bit levels with the Audacity-style Roseus palette. */
export function paint(canvas: HTMLCanvasElement, data: Spectrogram) {
  canvas.width = data.width; canvas.height = data.height;
  const pixels = atob(data.pixels), frame = new ImageData(data.width, data.height);
  for (let i = 0; i < pixels.length; i++) {
    const color = pixels.charCodeAt(i) * 3;
    frame.data.set([roseus[color], roseus[color + 1], roseus[color + 2], 255], i * 4);
  }
  canvas.getContext('2d')!.putImageData(frame, 0, 0);
}
