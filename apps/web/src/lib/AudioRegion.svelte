<script lang="ts" module>
  export type AudioSource = { id: number; kind: 'recording' | 'reference'; path: string; seconds: number };
  export type PlayerStatus = 'idle' | 'loading' | 'playing';
  export const clipTime = (seconds: number) => {
    const tenths = Math.round(seconds * 10);
    return `${Math.floor(tenths / 600)}:${((tenths % 600) / 10).toFixed(1).padStart(4, '0')}`;
  };
</script>

<script lang="ts">
  import { api, mediaURL } from './api.svelte';
  import { onMount, untrack, type Snippet } from 'svelte';
  import { app, t } from './app.svelte';
  import Button from './ui/Button.svelte';
  import ScaleSwitch from './ui/ScaleSwitch.svelte';
  import { kHz, paint, type Spectrogram, type Tick } from './spectrogram';
  import ZoomSpectrogram from './ZoomSpectrogram.svelte';
  import { seekInterval } from './annotation-geometry.js';
  // `selectable` false: a long recording to browse and play, with no part to choose. `actions` sit beside the play button.
  let { source, start = $bindable(0), end = $bindable(5), status = $bindable<PlayerStatus>('idle'), compact = false, fixedRange = false,
    zoomable=false, selectable=true, storageKey, onplaying, actions }:
    { source: AudioSource | {kind:'quiz';id:number;position:number;seconds:number}; start?: number; end?: number; status?: PlayerStatus; compact?: boolean; fixedRange?: boolean;
      zoomable?:boolean;selectable?:boolean;storageKey?:string;onplaying?:()=>void;actions?:Snippet } = $props();
  const choosing = $derived(!fixedRange && selectable);
  const mediaPath = $derived(source.kind==='quiz' ? `/quiz/sessions/${source.id}/questions/${source.position}` : `/sources/${source.kind}/${source.id}`);
  let canvas=$state<HTMLCanvasElement>(null!), audio: HTMLAudioElement;
  let viewStart = $state(0), span = $state(30), loading = $state(true), error = $state('');
  let cursor = $state(0), playhead = $state<number | null>(null);
  let ticks = $state<Tick[]>([]), loadedScale = '';
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
    loading = true; error = ''; loadedScale = app.frequencyScale;
    try {
      const data = await api<Spectrogram>(`${mediaPath}/spectrogram?start_s=${viewStart}&end_s=${viewEnd}&scale=${loadedScale}`);
      if (version !== request) return;
      ticks = data.frequency_ticks; paint(canvas, data);
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
  // Switching mel/linear anywhere reloads this figure at the same position.
  $effect(() => { if (initialized && !zoomable && app.frequencyScale !== loadedScale) untrack(() => void load()); });
  onMount(() => {
    if(fixedRange)span=Math.min(60,end-start);
    navigate(fixedRange?start:Math.floor(start / span) * span);
    initialized=true;
    return () => { request++; stop(); };
  });
</script>

{#snippet playbackButton()}
  <!-- In a list of sounds (compact) every row has one, so none is the main button (A7). -->
  <Button variant={compact?'default':'primary'} aria-busy={status === 'loading'} onclick={() => zoomable ? toggle() : status !== 'idle' ? stop() : fixedRange || choosing ? play() : playFromHead()}>
    {status === 'loading' ? t('loading') + ' · ' + t('pause') : status === 'playing' ? '❚❚ ' + t('pause') : '▶ ' + t(choosing?'playRange':'uListen')}
  </Button>
{/snippet}
<div class="region" class:compact class:zoomable style:--spec-height={compact?'var(--spec-sm)':fixedRange?'var(--spec-md)':'var(--spec-view)'}>
  {#if !fixedRange && source.seconds>span}<div class="region-controls">
    <Button disabled={viewStart<=0} onclick={()=>{playhead=null;navigate(viewStart-span);}}>‹ {t('previousView')}</Button>
    <label class="position">{t('viewStart')}<input type="range" min="0" max={Math.max(0,source.seconds-span)} step="1" value={viewStart} oninput={e=>{playhead=null;navigate(e.currentTarget.valueAsNumber);}}/></label>
    <Button disabled={viewEnd>=source.seconds} onclick={()=>{playhead=null;navigate(viewStart+span);}}>{t('nextView')} ›</Button>
  </div>{/if}
  {#if zoomable&&initialized&&viewEnd>viewStart}
    {#key app.frequencyScale}<ZoomSpectrogram {mediaPath} start={viewStart} end={viewEnd} cursor={status === 'idle' ? playhead : cursor} {storageKey}
      onseek={seek} onplay={toggle} controls={playbackButton}/>{/key}
  {:else}<div class="spectrogram-grid">
    <span class="unit">kHz</span><div class="head">{#if !compact}<ScaleSwitch/>{/if}</div>
    <div class="frequencies" aria-hidden="true">{#each ticks as tick}<span style:bottom={`${tick.row_fraction * 100}%`} class:edge-low={tick.row_fraction === 0} class:edge-high={tick.row_fraction === 1}>{kHz(tick)}</span>{/each}</div>
    <!-- Double-clicking plays from that point, like the zoomable view. -->
    <div class="spectrogram" class:fixed={!choosing} role="group" aria-label={t('spectrogram')}
      ondblclick={event => { if (!choosing && !loading && !error) { const from = pointerTime(event as unknown as PointerEvent), to = fixedRange ? end : viewEnd; if (!fixedRange) playhead = from; void play(Math.min(from, to - .05), to); } }}
      onpointerdown={event => { if (!choosing || loading || error || event.button !== 0) return; stop(); anchor = pointerTime(event); event.currentTarget.setPointerCapture(event.pointerId); select(event); }}
      onpointermove={select} onpointerup={event => { select(event); anchor = null; }} onpointercancel={() => anchor = null}>
      <canvas bind:this={canvas} aria-label={t('spectrogram')}></canvas>
      {#if !loading && !error}
        {#each ticks as tick}{#if tick.row_fraction > 0 && tick.row_fraction < 1}<div class="guide" style:bottom={`${tick.row_fraction * 100}%`}></div>{/if}{/each}
        {#if choosing}<div class="selection" style:left={`${percent(start)}%`} style:width={`${Math.max(0, percent(end) - percent(start))}%`}></div>{/if}
        {#if cursor >= viewStart && cursor <= viewEnd}<div class="playhead" style:left={`${percent(cursor)}%`}></div>{/if}
      {/if}
      {#if loading || error}<div class="spectrogram-state" role="status">{t(error || 'loadingSpectrogram')}</div>{/if}
    </div>
    <div></div><div class="time-axis"><span>{clipTime(viewStart)}</span><span>{clipTime((viewStart + viewEnd) / 2)}</span><span>{clipTime(viewEnd)}</span></div>
  </div>{/if}
  {#if !zoomable}<div class="region-controls">
    {@render playbackButton()}{@render actions?.()}
    {#if choosing}<label>{t('rangeStart')}<input type="number" min="0" max={source.seconds} step="any" bind:value={start} /></label>
    <label>{t('rangeEnd')}<input type="number" min="0" max={source.seconds} step="any" bind:value={end} /></label>
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
  .region { display: flex; flex-direction: column; gap: 8px; }
  .region-controls { display: flex; align-items: flex-end; flex-wrap: wrap; gap: 8px; }
  .zoomable .region-controls { order: -1; }
  .zoomable { container-type: inline-size; }
  .region-controls label { width: 112px; }
  .region-controls .position { flex: 1; min-width: 140px; }
  .range-hint { align-self: center; flex-basis: 100%; }
  .spectrogram-grid { display: grid; grid-template-columns: 44px minmax(0, 1fr); gap: 4px 8px; }
  .unit { align-self: end; text-align: right; font-size: 12px; color: rgb(var(--stone-600)); }
  .head { display: flex; justify-content: flex-end; min-height: 0; }
  .frequencies { position: relative; font-size: 12px; line-height: 1; color: rgb(var(--stone-600)); }
  .frequencies span { position: absolute; right: 0; transform: translateY(50%); font-variant-numeric: tabular-nums; }
  .frequencies .edge-low { transform: none; }
  .frequencies .edge-high { transform: translateY(100%); }
  .spectrogram { position: relative; height: var(--spec-height); background: #010101; touch-action: none; cursor: crosshair; overflow: hidden; border-radius: 4px; }
  .spectrogram.fixed { cursor: default; }
  canvas { width: 100%; height: 100%; display: block; }
  .guide { position: absolute; left: 0; right: 0; height: 1px; background: rgb(255 255 255 / .15); pointer-events: none; }
  .selection { position: absolute; top: 0; bottom: 0; border-left: 2px solid #fff5bd; border-right: 2px solid #fff5bd; pointer-events: none; }
  .playhead { position: absolute; top: 0; bottom: 0; border-left: 2px solid #60dcff; pointer-events: none; }
  .spectrogram-state { position: absolute; inset: 0; display: grid; place-items: center; background: #010101e8; color: #fff5bd; padding: 24px; }
  .time-axis { display: flex; justify-content: space-between; font-variant-numeric: tabular-nums; font-size: 12px; color: rgb(var(--stone-600)); }
</style>
