<script lang="ts">
  import { onMount, tick, untrack } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from './api';
  import AudioRegion, { clipTime, type AudioSource } from './AudioRegion.svelte';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import XenoCanto, { type Attribution } from './XenoCanto.svelte';
  import SearchReview, { type Run, type Hit as SavedHit } from './SearchReview.svelte';
  import { commonName, type Species } from './species';
  let { recording, datasetId, active, locale, t }: { recording: Omit<AudioSource, 'kind'>;
    datasetId: number; active: boolean; locale: string; t: (key: string) => string } = $props();
  type Hit = { segment_id: number; recording_id: number; path: string; seconds: number;
    recorded_at: string; timezone: string; start_s: number; end_s: number; similarity: number };
  type SearchResult = { source: AudioSource; query: { start_s: number; end_s: number;
    windows: { start_s: number; end_s: number; padding_s: number }[] };
    hits: Hit[]; total_recordings: number; searched_recordings: number; searched_segments: number; unsearched_recordings: number };
  type Reference = AudioSource & { attribution?: Attribution };
  type Target = { species: Species; clips: { source: Reference; start_s: number; end_s: number }[] };
  let references = $state<Reference[]>([]), chosen = $state('recording'), referencePath = $state('');
  let species = $state<Species | null>(null), targets = $state<Target[]>([]);
  let review = $state<{ showRuns: (runs: Run[]) => Promise<void> }>(null!);
  let start = $state(0), end = $state(untrack(() => Math.min(5, recording.seconds))), playerKey = $state(0);
  let preview = $state<Hit | null>(null), previewStart = $state(0), previewEnd = $state(5);
  let searching = $state(false), adding = $state(false), error = $state(''), result = $state<SearchResult | null>(null);
  let player = $state<{ play: () => Promise<void> }>(null!);
  const source: Reference = $derived(chosen === 'recording' ? { ...recording, kind: 'recording' as const } : references.find(r => r.id === Number(chosen))!);
  const name = (path: string) => path.split(/[\\/]/).pop();
  const date = (hit: Hit) => new Intl.DateTimeFormat(locale, { dateStyle: 'short', timeStyle: 'short', timeZone: hit.timezone }).format(new Date(hit.recorded_at));
  const validRange = $derived(Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end <= source.seconds && end - start >= 0.2 - 1e-9 && end - start <= 30 + 1e-9);
  const selectedTarget = $derived(targets.find(row => row.species.id === species?.id));
  const atLimit = $derived(selectedTarget ? selectedTarget.clips.length >= 20 : targets.length >= 20);
  function changeSource() { preview = null; start = 0; end = Math.min(5, source.seconds); result = null; error = ''; playerKey++; }
  function selectReference(row: Reference) {
    references = [row, ...references.filter(r => r.id !== row.id)]; chosen = String(row.id); changeSource();
  }
  function saveDraft() { localStorage.setItem(`echoroo-search-${datasetId}`, JSON.stringify(targets)); }
  function addClip() {
    if (!species || atLimit) return;
    let target = targets.find(row => row.species.id === species!.id);
    if (!target) { target = { species, clips: [] }; targets.push(target); target = targets[targets.length - 1]; }
    if (!target.clips.some(c => c.source.kind === source.kind && c.source.id === source.id && c.start_s === start && c.end_s === end)) {
      target.clips.push({ source, start_s: start, end_s: end });
    }
    saveDraft();
  }
  async function addReference() {
    adding = true; error = '';
    try {
      if (isTauri()) {
        const path = await open({ multiple: false, filters: [{ name: t('referenceAudio'), extensions: ['wav', 'flac', 'mp3'] }] });
        if (!path) return;
        referencePath = path;
      }
      selectReference(await api<Reference>('/references', { path: referencePath }));
    } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { adding = false; }
  }
  async function search() {
    searching = true; error = ''; result = null;
    try {
      result = await api<SearchResult>('/search', { dataset_id: datasetId, source_kind: source.kind,
        source_id: source.id, start_s: start, end_s: end, limit: 30 });
    } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { searching = false; }
  }
  async function saveAndSearch() {
    searching = true; error = ''; result = null;
    try {
      const runs = await api<Run[]>('/search-runs', { dataset_id: datasetId, targets: targets.map(target => ({
        species_id: target.species.id, clips: target.clips.map(c => ({ source_kind: c.source.kind, source_id: c.source.id, start_s: c.start_s, end_s: c.end_s })) })) });
      await review.showRuns(runs);
    } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { searching = false; }
  }
  async function listen(hit: Hit) {
    preview = hit; previewStart = hit.start_s; previewEnd = hit.end_s;
    playerKey++; await tick(); await player.play();
  }
  export async function playRecording(from: number, to: number) {
    chosen = 'recording'; preview = null; start = from; end = to;
    playerKey++; await tick(); await player.play();
  }
  onMount(() => {
    void api<Reference[]>('/references').then(rows => references = rows).catch(e => error = e.message);
    try { targets = JSON.parse(localStorage.getItem(`echoroo-search-${datasetId}`) || '[]'); } catch { targets = []; }
  });
</script>

<div class="section-heading"><h3>{t('referenceAudio')}</h3><button class="small" disabled={adding || searching} onclick={addReference}>{t('addReference')}</button></div>
<SpeciesPicker bind:value={species} {locale} {t} disabled={searching} />
<XenoCanto {species} {locale} {t} onselect={selectReference} />
{#if !isTauri()}<label>{t('referencePath')}<input bind:value={referencePath} /></label>{/if}
<label>{t('referenceSource')}<select bind:value={chosen} onchange={changeSource} disabled={searching}>
  <option value="recording">{t('currentRecording')} · {name(recording.path)}</option>
  {#each references as row}<option value={String(row.id)}>{name(row.path)}</option>{/each}
</select></label>
<p class="muted reference-hint">{t('referenceHint')}</p>
{#if source.attribution}<p class="muted">XC{source.attribution.id} · {source.attribution.recordist} · {source.attribution.license}</p>{/if}
{#key playerKey}
  {#if preview}
    <div class="listening"><strong>{t('listeningHit')} · {name(preview.path)} · {clipTime(preview.start_s)}–{clipTime(preview.end_s)}</strong>
      <button class="small" onclick={() => preview = null}>{t('backToReference')}</button></div>
    <AudioRegion source={{ id: preview.recording_id, kind: 'recording', path: preview.path, seconds: preview.seconds }} {t} bind:start={previewStart} bind:end={previewEnd} bind:this={player} />
  {:else}<AudioRegion {source} {t} bind:start bind:end bind:this={player} />{/if}
{/key}
<div class="search-action"><button disabled={searching || !species || !validRange || preview !== null || atLimit} onclick={addClip}>{t('addQueryClip')}</button>
  <button class="small" disabled={searching || active || !validRange || preview !== null} onclick={search}>{t(searching ? 'searchingAudio' : 'quickSearch')}</button>
  <span class="muted">{t('queryRangeHint')}</span></div>
{#if targets.length}
  <div class="query-set"><h3>{t('querySet')}</h3><p class="muted">{t('querySetHint')}</p>
    {#each targets as target, ti}<div class="query-target"><strong>{commonName(target.species, locale)} · <i>{target.species.scientific_name}</i></strong>
      {#each target.clips as clip, ci}<div class="section-heading"><span class="muted">{name(clip.source.path)} · {clipTime(clip.start_s)}–{clipTime(clip.end_s)}</span>
        <button class="small" disabled={searching} aria-label={`${t('removeReference')} ${ci + 1}`} onclick={() => {
          targets[ti].clips.splice(ci, 1); targets = targets.filter(row => row.clips.length); saveDraft();
        }}>{t('removeReference')}</button></div>{/each}</div>{/each}
    <button class="primary" disabled={searching || active} onclick={saveAndSearch}>{t(searching ? 'searchingAudio' : 'saveAndSearch')}</button>
  </div>
{/if}
{#if error}<p class="error" role="alert">{t(error)}</p>{/if}
{#if searching}<p class="muted" role="status">{t('searchProgress')}</p>{/if}
<SearchReview {datasetId} {locale} {t} onlisten={(hit: SavedHit) => listen(hit)} bind:this={review} />
{#if result}
  <section class="search-results" aria-label={t('similarSounds')}>
    <h3>{t('similarSounds')} ({result.hits.length})</h3>
    <p class="muted">{t('searchedReference')}: {name(result.source.path)} · {clipTime(result.query.start_s)}–{clipTime(result.query.end_s)}</p>
    <p class="muted">{t('searchCoverage')}: {result.searched_recordings} / {result.total_recordings} {t('files')} · {result.searched_segments} {t('analyzedSegments')}</p>
    {#if result.unsearched_recordings}<p class="notice">{t('partialSearch')}</p>{/if}
    <p class="muted">{t('similarityHint')}</p>
    <details><summary>{t('queryWindows')}</summary>{#each result.query.windows as window}<p class="muted">{clipTime(window.start_s)}–{clipTime(window.end_s)} · {t('zeroPadding')}: {window.padding_s.toFixed(2)} {t('seconds')}</p>{/each}</details>
    {#if !result.hits.length}<p class="empty">{t('noSimilarSounds')}</p>{/if}
    <div class="hit-list"><table><thead><tr><th>{t('recording')}</th><th>{t('similarity')}</th><th>{t('segment')}</th></tr></thead><tbody>
      {#each result.hits as hit}<tr class:chosen={preview?.segment_id === hit.segment_id}><td>{name(hit.path)}<div class="muted">{date(hit)} · {hit.timezone}</div></td>
        <td>{hit.similarity.toFixed(3)}</td><td><button class="small" onclick={() => listen(hit)} aria-label={`${t('play')} ${name(hit.path)} ${clipTime(hit.start_s)}`}>{clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td></tr>{/each}
    </tbody></table></div>
  </section>
{/if}

<style>
  .reference-hint { margin-top: 8px; }
  .search-action, .listening { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; margin: 12px 0; }
  .listening { background: rgb(var(--primary-100)); padding: 10px; font-size: 12px; }
  .search-results { margin: 18px 0; border-top: 1px solid rgb(var(--stone-300)); padding-top: 16px; }
  .hit-list { max-height: 350px; overflow: auto; margin: 8px 0; }
  .hit-list td:last-child { white-space: nowrap; }
  details { font-size: 12px; }
  .query-set { border: 1px solid rgb(var(--stone-300)); padding: 14px; border-radius: 6px; margin: 12px 0; }
  .query-target { font-size: 12px; margin: 10px 0; }
</style>
