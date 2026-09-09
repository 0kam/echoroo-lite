<script lang="ts" module>
  export type AudioSource = { id: number; kind: 'recording' | 'reference'; path: string; seconds: number };
  export const clipTime = (seconds: number) => {
    const tenths = Math.round(seconds * 10);
    return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, '0')}`;
  };
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { api, sourceAudioURL } from './api';
  let { source, t, start = $bindable(0), end = $bindable(5) }:
    { source: AudioSource; t: (key: string) => string; start?: number; end?: number } = $props();
  let canvas: HTMLCanvasElement, audio: HTMLAudioElement;
  let viewStart = $state(0), span = $state(30), loading = $state(true), error = $state('');
  let cursor = $state(0), playing = $state(false);
  let anchor: number | null = null, request = 0, stopAt: number | null = null;
  let stopTimer: ReturnType<typeof setTimeout> | undefined;
  const viewEnd = $derived(Math.min(source.seconds, viewStart + span));
  const percent = (time: number) => Math.max(0, Math.min(100, (time - viewStart) / (viewEnd - viewStart) * 100));

  async function load() {
    const version = ++request;
    loading = true; error = '';
    try {
      const data = await api<{ width: number; height: number; pixels: string }>(
        `/sources/${source.kind}/${source.id}/spectrogram?start_s=${viewStart}&end_s=${viewEnd}`);
      if (version !== request) return;
      canvas.width = data.width; canvas.height = data.height;
      const pixels = atob(data.pixels), frame = new ImageData(data.width, data.height);
      // Warm, high-contrast intensity map; relative level within the displayed interval.
      for (let i = 0; i < pixels.length; i++) {
        const level = pixels.charCodeAt(i) / 255;
        frame.data.set([Math.min(255, 18 + level * 340), Math.max(0, level * 330 - 60),
          Math.max(0, level * 400 - 170), 255], i * 4);
      }
      canvas.getContext('2d')!.putImageData(frame, 0, 0);
    } catch (e) { if (version === request) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if (version === request) loading = false; }
  }
  function navigate(value: number) {
    viewStart = Math.max(0, Math.min(source.seconds - Math.min(span, source.seconds), Number.isFinite(value) ? value : 0));
    void load();
  }
  function pointerTime(event: PointerEvent) {
    const bounds = event.currentTarget instanceof HTMLElement ? event.currentTarget.getBoundingClientRect() : canvas.getBoundingClientRect();
    return Math.round((viewStart + Math.max(0, Math.min(1, (event.clientX - bounds.left) / bounds.width)) * (viewEnd - viewStart)) * 100) / 100;
  }
  function select(event: PointerEvent) {
    if (anchor === null) return;
    const time = Math.min(source.seconds, pointerTime(event));
    start = Math.min(anchor, time); end = Math.max(anchor, time);
  }
  function scheduleStop() {
    clearTimeout(stopTimer);
    if (stopAt === null || audio.paused) return;
    stopTimer = setTimeout(() => {
      // The media clock can lag behind wall time during buffering.
      if (stopAt !== null && audio.currentTime < stopAt - 0.01) { scheduleStop(); return; }
      audio.pause(); stopAt = null;
    }, Math.max(20, (stopAt - audio.currentTime) / audio.playbackRate * 1000));
  }
  export async function play() {
    error = '';
    if (!(Number.isFinite(start) && Number.isFinite(end) && 0 <= start && start < end && end <= source.seconds)) {
      error = 'invalid_interval'; return;
    }
    try {
      if (!audio.readyState) await new Promise<void>((resolve, reject) => {
        const done = () => { clean(); resolve(); };
        const fail = () => { clean(); reject(Error('unreadable_audio')); };
        const timer = setTimeout(fail, 10000);
        function clean() { clearTimeout(timer); audio.removeEventListener('loadedmetadata', done); audio.removeEventListener('error', fail); }
        audio.addEventListener('loadedmetadata', done); audio.addEventListener('error', fail);
      });
      audio.currentTime = start; stopAt = end;
      await audio.play(); scheduleStop();
    } catch { error = 'unreadable_audio'; }
  }
  onMount(() => {
    navigate(Math.floor(start / span) * span);
    return () => { request++; clearTimeout(stopTimer); audio.pause(); };
  });
</script>

<div class="region-controls">
  <button class="small" disabled={viewStart <= 0} onclick={() => navigate(viewStart - span)}>{t('previousView')}</button>
  <label>{t('viewStart')}<input type="number" min="0" max={source.seconds} step="any" value={viewStart} onchange={e => navigate(e.currentTarget.valueAsNumber)} /></label>
  <label>{t('viewSpan')}<select bind:value={span} onchange={() => navigate(viewStart)}>{#each [5, 15, 30, 60] as seconds}<option value={seconds}>{seconds} {t('seconds')}</option>{/each}</select></label>
  <button class="small" disabled={viewEnd >= source.seconds} onclick={() => navigate(viewStart + span)}>{t('nextView')}</button>
</div>
<div class="spectrogram-grid">
  <div class="frequencies"><span>12 kHz</span><span>6 kHz</span><span>0</span></div>
  <div class="spectrogram" role="group" aria-label={t('spectrogram')}
    onpointerdown={event => { if (loading || error || event.button !== 0) return; audio.pause(); anchor = pointerTime(event); event.currentTarget.setPointerCapture(event.pointerId); select(event); }}
    onpointermove={select} onpointerup={event => { select(event); anchor = null; }} onpointercancel={() => anchor = null}>
    <canvas bind:this={canvas} aria-label={t('spectrogram')}></canvas>
    {#if !loading && !error}
      <div class="selection" style:left={`${percent(start)}%`} style:width={`${Math.max(0, percent(end) - percent(start))}%`}></div>
      {#if cursor >= viewStart && cursor <= viewEnd}<div class="playhead" style:left={`${percent(cursor)}%`}></div>{/if}
    {/if}
    {#if loading || error}<div class="spectrogram-state" role="status">{t(error || 'loadingSpectrogram')}</div>{/if}
  </div>
  <div></div><div class="time-axis"><span>{clipTime(viewStart)}</span><span>{clipTime((viewStart + viewEnd) / 2)}</span><span>{clipTime(viewEnd)}</span></div>
</div>
<p class="muted">{t('rangeHint')}</p>
<div class="region-controls">
  <label>{t('rangeStart')}<input type="number" min="0" max={source.seconds} step="any" bind:value={start} /></label>
  <label>{t('rangeEnd')}<input type="number" min="0" max={source.seconds} step="any" bind:value={end} /></label>
  <button onclick={() => playing ? audio.pause() : play()}>{t(playing ? 'pause' : 'playRange')}</button>
  <button class="small" onclick={() => navigate(start)}>{t('showRange')}</button>
</div>
<!-- Field recordings have no speech transcript. -->
<!-- svelte-ignore a11y_media_has_caption -->
<audio bind:this={audio} controls preload="metadata" src={sourceAudioURL(source.kind, source.id)} aria-label={t('audio')}
  onerror={() => error = 'unreadable_audio'} onplaying={() => { playing = true; scheduleStop(); }}
  onpause={() => { playing = false; clearTimeout(stopTimer); }} onseeked={scheduleStop} onratechange={scheduleStop}
  ontimeupdate={() => cursor = audio.currentTime}></audio>
{#if error}<p class="error" role="alert">{t(error)}</p>{/if}

<style>
  .region-controls { display: flex; align-items: end; flex-wrap: wrap; gap: 10px; margin: 12px 0; }
  .region-controls input { width: 112px; }
  .region-controls select { width: 100px; }
  .spectrogram-grid { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 4px 8px; }
  .frequencies { display: flex; flex-direction: column; justify-content: space-between; text-align: right; font-size: 10px; color: rgb(var(--stone-600)); }
  .spectrogram { position: relative; height: 210px; background: #120000; touch-action: none; cursor: crosshair; overflow: hidden; }
  canvas { width: 100%; height: 100%; display: block; }
  .selection { position: absolute; top: 0; bottom: 0; background: #fff3; border-left: 2px solid #fff5bd; border-right: 2px solid #fff5bd; pointer-events: none; }
  .playhead { position: absolute; top: 0; bottom: 0; border-left: 2px solid #60dcff; pointer-events: none; }
  .spectrogram-state { position: absolute; inset: 0; display: grid; place-items: center; background: #120000e8; color: #fff5bd; padding: 20px; }
  .time-axis { display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; font-size: 11px; color: rgb(var(--stone-600)); }
  audio { margin-bottom: 10px; }
</style>
