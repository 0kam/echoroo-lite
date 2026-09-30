<script lang="ts" module>
  export type AudioSource = { id: number; kind: 'recording' | 'reference'; path: string; seconds: number };
  export type PlayerStatus = 'idle' | 'loading' | 'playing';
  export const clipTime = (seconds: number) => {
    const tenths = Math.round(seconds * 10);
    return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, '0')}`;
  };
</script>

<script lang="ts">
  import { onMount } from 'svelte';
  import { api, mediaURL } from './api.svelte';
  import { t } from './app.svelte';
  import Button from './ui/Button.svelte';
  import { roseus } from './roseus';
  import ZoomSpectrogram from './ZoomSpectrogram.svelte';
  import { seekInterval } from './annotation-geometry.js';
  let { source, start = $bindable(0), end = $bindable(5), status = $bindable<PlayerStatus>('idle'), compact = false, fixedRange = false,
    zoomable=false, storageKey, onplaying }:
    { source: AudioSource | {kind:'quiz';id:number;position:number;seconds:number}; start?: number; end?: number; status?: PlayerStatus; compact?: boolean; fixedRange?: boolean;
      zoomable?:boolean;storageKey?:string;onplaying?:()=>void } = $props();
  const mediaPath = $derived(source.kind==='quiz' ? `/quiz/sessions/${source.id}/questions/${source.position}` : `/sources/${source.kind}/${source.id}`);
  let canvas=$state<HTMLCanvasElement>(null!), audio: HTMLAudioElement;
  let viewStart = $state(0), span = $state(30), loading = $state(true), error = $state('');
  let cursor = $state(0), playhead = $state<number | null>(null);
  let frequencyMax = $state(12000);
  let initialized=$state(false);
  let anchor: number | null = null, request = 0, stopAt: number | null = null;
  let stopTimer: ReturnType<typeof setTimeout> | undefined;
  let playbackVersion = 0;
  let cancelMetadata: (() => void) | undefined;
  const viewEnd = $derived(Math.min(source.seconds, viewStart + span));
  const percent = (time: number) => Math.max(0, Math.min(100, (time - viewStart) / (viewEnd - viewStart) * 100));

  async function load() {
    if(zoomable){loading=false;return;}
    const version = ++request;
    loading = true; error = '';
    try {
      const data = await api<{ width: number; height: number; pixels: string; frequency_max_hz: number }>(
        `${mediaPath}/spectrogram?start_s=${viewStart}&end_s=${viewEnd}`);
      if (version !== request) return;
      frequencyMax = data.frequency_max_hz;
      canvas.width = data.width; canvas.height = data.height;
      const pixels = atob(data.pixels), frame = new ImageData(data.width, data.height);
      // Audacity-style Roseus palette; relative level within the displayed interval.
      for (let i = 0; i < pixels.length; i++) {
        const color = pixels.charCodeAt(i) * 3;
        frame.data[i * 4] = roseus[color];
        frame.data[i * 4 + 1] = roseus[color + 1];
        frame.data[i * 4 + 2] = roseus[color + 2];
        frame.data[i * 4 + 3] = 255;
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
      stop();
    }, Math.max(20, (stopAt - audio.currentTime) / audio.playbackRate * 1000));
  }
  /** Plays the selected interval; `status` reports loading and playback so callers can show it. */
  export async function play(from = start, to = end) {
    stop();
    const player = audio;
    if (!player) return;
    const version = playbackVersion;
    error = '';
    start = from; end = to;
    if (!(Number.isFinite(from) && Number.isFinite(to) && 0 <= from && from < to && to <= source.seconds)) {
      error = 'invalid_interval'; return;
    }
    if (from < viewStart || from > viewEnd) navigate(Math.floor(from / span) * span);
    status = 'loading';
    try {
      if (!audio.readyState) await new Promise<void>((resolve, reject) => {
        const done = () => { clean(); resolve(); };
        const fail = () => { clean(); reject(Error('unreadable_audio')); };
        const timer = setTimeout(fail, 10000);
        function clean() { clearTimeout(timer); cancelMetadata = undefined; player.removeEventListener('loadedmetadata', done); player.removeEventListener('error', fail); }
        cancelMetadata = done;
        audio.addEventListener('loadedmetadata', done); audio.addEventListener('error', fail);
      });
      if (version !== playbackVersion) return;
      audio.currentTime = from; stopAt = to;
      await audio.play(); if (version === playbackVersion) scheduleStop();
    } catch { if (version === playbackVersion) { error = 'unreadable_audio'; status = 'idle'; } }
  }
  /** Zoomable view: plays from the playhead (set by double-click) to the end of the clip. */
  function playFromHead() {
    const from = playhead !== null && playhead < viewEnd - 0.05 ? playhead : viewStart;
    void play(from, viewEnd);
  }
  function seek(time: number) {
    const interval=seekInterval(time,viewEnd);
    playhead = interval[0]; cursor = interval[0];
    void play(...interval);
  }
  function toggle() {
    if (status === 'idle') { playFromHead(); return; }
    if (zoomable && status === 'playing') playhead = cursor; // Pausing keeps the position for the next play.
    stop();
  }
  export function stop() {
    playbackVersion++; cancelMetadata?.(); audio?.pause(); clearTimeout(stopTimer); stopAt = null; status = 'idle';
  }
  onMount(() => {
    if(fixedRange)span=Math.min(60,end-start);
    navigate(fixedRange?start:Math.floor(start / span) * span);
    initialized=true;
    return () => { request++; stop(); };
  });
</script>

{#snippet playbackButton()}
  <Button variant="primary" aria-busy={status === 'loading'} onclick={() => zoomable ? toggle() : status !== 'idle' ? stop() : play()}>
    {status === 'loading' ? t('loading') + ' · ' + t('pause') : status === 'playing' ? '❚❚ ' + t('pause') : '▶ ' + t(fixedRange?'uListen':'playRange')}
  </Button>
{/snippet}
<div class="region" class:compact class:zoomable>
  {#if !fixedRange && source.seconds>span}<div class="region-controls">
    <Button disabled={viewStart<=0} onclick={()=>navigate(viewStart-span)}>‹ {t('previousView')}</Button>
    <label class="position">{t('viewStart')}<input type="range" min="0" max={Math.max(0,source.seconds-span)} step="1" value={viewStart} oninput={e=>navigate(e.currentTarget.valueAsNumber)}/></label>
    <Button disabled={viewEnd>=source.seconds} onclick={()=>navigate(viewStart+span)}>{t('nextView')} ›</Button>
  </div>{/if}
  {#if zoomable&&initialized&&viewEnd>viewStart}
    <ZoomSpectrogram {mediaPath} start={viewStart} end={viewEnd} cursor={status === 'idle' ? playhead : cursor} {storageKey}
      onseek={seek} onplay={toggle} controls={playbackButton}/>
  {:else}<div class="spectrogram-grid">
    <div class="frequencies"><span>{+(frequencyMax / 1000).toFixed(2)} kHz</span><span>{+(frequencyMax / 2000).toFixed(2)} kHz</span><span>0</span></div>
    <!-- Double-clicking a fixed clip plays from that point, like the zoomable view. -->
    <div class="spectrogram" class:fixed={fixedRange} role="group" aria-label={t('spectrogram')}
      ondblclick={event => { if (fixedRange && !loading && !error) void play(Math.min(pointerTime(event as unknown as PointerEvent), end - .05), end); }}
      onpointerdown={event => { if (fixedRange || loading || error || event.button !== 0) return; stop(); anchor = pointerTime(event); event.currentTarget.setPointerCapture(event.pointerId); select(event); }}
      onpointermove={select} onpointerup={event => { select(event); anchor = null; }} onpointercancel={() => anchor = null}>
      <canvas bind:this={canvas} aria-label={t('spectrogram')}></canvas>
      {#if !loading && !error}
        {#if !fixedRange}<div class="selection" style:left={`${percent(start)}%`} style:width={`${Math.max(0, percent(end) - percent(start))}%`}></div>{/if}
        {#if cursor >= viewStart && cursor <= viewEnd}<div class="playhead" style:left={`${percent(cursor)}%`}></div>{/if}
      {/if}
      {#if loading || error}<div class="spectrogram-state" role="status">{t(error || 'loadingSpectrogram')}</div>{/if}
    </div>
    <div></div><div class="time-axis"><span>{clipTime(viewStart)}</span><span>{clipTime((viewStart + viewEnd) / 2)}</span><span>{clipTime(viewEnd)}</span></div>
  </div>{/if}
  {#if !zoomable}<div class="region-controls">
    {@render playbackButton()}
    {#if !fixedRange}<label>{t('rangeStart')}<input type="number" min="0" max={source.seconds} step="any" bind:value={start} readonly={fixedRange} /></label>
    <label>{t('rangeEnd')}<input type="number" min="0" max={source.seconds} step="any" bind:value={end} readonly={fixedRange} /></label>
    <span class="muted range-hint">{t('rangeHint')}</span>{/if}
  </div>{/if}
  <!-- Field recordings have no speech transcript. -->
  <!-- svelte-ignore a11y_media_has_caption -->
  <audio bind:this={audio} preload="metadata" src={mediaURL(`${mediaPath}/audio`)} aria-label={t('audio')}
    onerror={() => { error = 'unreadable_audio'; status = 'idle'; }} onplaying={() => { if (!audio.paused) { status = 'playing'; scheduleStop(); onplaying?.(); } }}
    onwaiting={() => { if (status === 'playing') status = 'loading'; }}
    onpause={() => { if (audio.paused && (status !== 'loading' || stopAt !== null)) { status = 'idle'; clearTimeout(stopTimer); } }} onended={stop} onseeked={scheduleStop} onratechange={scheduleStop}
    ontimeupdate={() => cursor = audio.currentTime}></audio>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
</div>

<style>
  .region { display: flex; flex-direction: column; gap: 10px; }
  .region-controls { display: flex; align-items: flex-end; flex-wrap: wrap; gap: 10px; }
  .zoomable .region-controls { order: -1; }
  .region-controls label { width: 110px; }
  .range-hint { align-self: center; }
  .region-controls .position {flex:1;min-width:140px}
  .range-hint { flex-basis: 100%; }
  .spectrogram-grid { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 4px 8px; }
  .frequencies { display: flex; flex-direction: column; justify-content: space-between; text-align: right; font-size: 10px; color: rgb(var(--stone-600)); }
  .spectrogram { position: relative; height: 210px; background: #010101; touch-action: none; cursor: crosshair; overflow: hidden; border-radius: 4px; }
  .spectrogram.fixed { cursor: default; }
  .compact .spectrogram { height: 150px; }
  canvas { width: 100%; height: 100%; display: block; }
  .selection { position: absolute; top: 0; bottom: 0; border-left: 2px solid #fff5bd; border-right: 2px solid #fff5bd; pointer-events: none; }
  .playhead { position: absolute; top: 0; bottom: 0; border-left: 2px solid #60dcff; pointer-events: none; }
  .spectrogram-state { position: absolute; inset: 0; display: grid; place-items: center; background: #010101e8; color: #fff5bd; padding: 20px; }
  .time-axis { display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; font-size: 11px; color: rgb(var(--stone-600)); }
</style>
