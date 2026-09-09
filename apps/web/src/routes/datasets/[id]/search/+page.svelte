<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount, tick } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from '$lib/api.svelte';
  import { app, t, activeJob, datasetOf, fileName, siteTime, loadRecordings, type Recording } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import AudioRegion, { clipTime, type AudioSource, type PlayerStatus } from '$lib/AudioRegion.svelte';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import XenoCantoSearch, { type Attribution } from '$lib/XenoCantoSearch.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type Hit = { segment_id: number; recording_id: number; path: string; seconds: number; recorded_at: string; timezone: string; start_s: number; end_s: number; similarity: number };
  type SearchResult = { source: AudioSource; query: { start_s: number; end_s: number; windows: { start_s: number; end_s: number; padding_s: number }[] };
    start_date: string | null; end_date: string | null; hits: Hit[]; total_recordings: number; searched_recordings: number; searched_segments: number; unsearched_recordings: number };
  type Reference = AudioSource & { attribution?: Attribution };
  type Target = { species: Species; clips: { source: Reference; start_s: number; end_s: number }[] };
  type RunSummary = { id: number; species: Species; created_at: string };
  const datasetId = $derived(Number(page.params.id));
  const dataset = $derived(datasetOf(datasetId));
  let records = $state<Recording[]>([]), references = $state<Reference[]>([]), runs = $state<RunSummary[]>([]);
  let mode = $state<'recording' | 'reference'>('recording'), recordingDate = $state(''), recordingId = $state(''), referenceId = $state('');
  let referencePath = $state(''), loadError = $state('');
  let species = $state<Species | null>(null), targets = $state<Target[]>([]);
  let first = $state(''), last = $state('');
  let start = $state(0), end = $state(5), status = $state<PlayerStatus>('idle'), playerKey = $state(0);
  let preview = $state<Hit | null>(null), previewStart = $state(0), previewEnd = $state(5), activeHit = $state<number | null>(null);
  let player = $state<{ play: (from?: number, to?: number) => Promise<void>; stop: () => void }>(null!);
  let result = $state<SearchResult | null>(null);
  const dayRecords = $derived(records.filter(r => !recordingDate || r.original_time.startsWith(recordingDate)));
  const source = $derived<Reference | null>(mode === 'recording'
    ? (() => { const r = records.find(r => String(r.id) === recordingId); return r ? { id: r.id, kind: 'recording' as const, path: r.path, seconds: r.seconds } : null; })()
    : references.find(r => String(r.id) === referenceId) ?? null);
  const validPeriod = $derived(!first || !last || first <= last);
  const validRange = $derived(!!source && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end <= source.seconds && end - start >= 0.2 - 1e-9 && end - start <= 30 + 1e-9);
  const selectedTarget = $derived(targets.find(row => row.species.id === species?.id));
  const atLimit = $derived(selectedTarget ? selectedTarget.clips.length >= 20 : targets.length >= 20);
  const clipCount = $derived(targets.reduce((sum, target) => sum + target.clips.length, 0));
  const busyJob = $derived(!!activeJob());
  function resetRange() { preview = null; activeHit = null; result = null; start = 0; end = Math.min(5, source?.seconds ?? 5); playerKey++; }
  function saveDraft() { localStorage.setItem(`echoroo-search-${datasetId}`, JSON.stringify(targets)); }
  function savePeriod() { localStorage.setItem(`echoroo-search-period-${datasetId}`, JSON.stringify([first, last])); }
  function selectReference(row: Reference) {
    references = [row, ...references.filter(r => r.id !== row.id)]; mode = 'reference'; referenceId = String(row.id); resetRange();
  }
  function addClip() {
    if (!species || !source || atLimit) return;
    let target = targets.find(row => row.species.id === species!.id);
    if (!target) { targets.push({ species, clips: [] }); target = targets[targets.length - 1]; }
    if (!target.clips.some(c => c.source.kind === source!.kind && c.source.id === source!.id && c.start_s === start && c.end_s === end)) {
      target.clips.push({ source, start_s: start, end_s: end });
    }
    saveDraft(); notify('saved', 'info');
  }
  const addReference = createTask(async () => {
    if (isTauri()) {
      const path = await open({ multiple: false, filters: [{ name: t('referenceAudio'), extensions: ['wav', 'flac', 'mp3'] }] });
      if (!path) return;
      referencePath = path;
    }
    selectReference(await api<Reference>('/references', { path: referencePath }));
  }, { toast: true });
  const quick = createTask(async () => {
    if (!source) return;
    result = null; preview = null; activeHit = null;
    result = await api<SearchResult>('/search', { dataset_id: datasetId, source_kind: source.kind, source_id: source.id,
      start_s: start, end_s: end, limit: 30, start_date: first || null, end_date: last || null });
  });
  const saveAndSearch = createTask(async () => {
    const saved = await api<{ id: number }[]>('/search-runs', { dataset_id: datasetId, start_date: first || null, end_date: last || null,
      targets: targets.map(target => ({ species_id: target.species.id,
        clips: target.clips.map(c => ({ source_kind: c.source.kind, source_id: c.source.id, start_s: c.start_s, end_s: c.end_s })) })) });
    notify('searchSaved');
    await goto(resolve(`/datasets/${datasetId}/search/${saved[0].id}`));
  });
  async function listen(hit: Hit) {
    if (activeHit === hit.segment_id && status !== 'idle') { player.stop(); return; }
    preview = hit; previewStart = hit.start_s; previewEnd = hit.end_s; activeHit = hit.segment_id;
    playerKey++; await tick(); await player.play(hit.start_s, hit.end_s);
  }
  onMount(() => {

    try { targets = JSON.parse(localStorage.getItem(`echoroo-search-${datasetId}`) || '[]'); } catch { targets = []; }
    try { [first, last] = JSON.parse(localStorage.getItem(`echoroo-search-period-${datasetId}`) || '["", ""]'); } catch { first = ''; last = ''; }
    void Promise.all([loadRecordings(datasetId), api<Reference[]>('/references'), api<RunSummary[]>(`/search-runs?dataset_id=${datasetId}`)]).then(([rows, refs, history]) => {
      records = rows; references = refs; runs = history;
      const wanted = new URL(page.url.hash.slice(1), page.url).searchParams.get('recording');
      const initial = rows.find(r => String(r.id) === wanted) ?? rows[0];
      if (initial) { recordingDate = initial.original_time.slice(0, 10); recordingId = String(initial.id); end = Math.min(5, initial.seconds); }
    }).catch(e => loadError = e instanceof Error ? e.message : 'unknown_error');
  });
</script>

<div class="page">
  <div class="page-header">
    <div>
      <div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span><a href={resolve(`/datasets/${datasetId}`)}>{dataset?.name}</a><span>›</span></div>
      <h1>{t('navSearch')}</h1><p>{t('searchIntro')}</p>
    </div>
  </div>
  {#if loadError}<p class="error" role="alert">{t(loadError)}</p>{/if}
  <div class="two-col">
    <div class="steps">
      <section class="panel step-card">
        <h2>{t('stepSpecies')}</h2><p class="muted">{t('querySetHint')}</p>
        <SpeciesPicker bind:value={species} />
      </section>
      <section class="panel step-card">
        <h2>{t('stepReference')}</h2><p class="muted">{t('referenceHint')}</p>
        <div class="source-tabs" role="tablist">
          <button type="button" class="button sm" class:active={mode === 'recording'} aria-pressed={mode === 'recording'} onclick={() => { mode = 'recording'; resetRange(); }}>{t('referenceFromDataset')}</button>
          <button type="button" class="button sm" class:active={mode === 'reference'} aria-pressed={mode === 'reference'} disabled={!references.length} onclick={() => { mode = 'reference'; if (!referenceId && references.length) referenceId = String(references[0].id); resetRange(); }}>{t('referenceAudio')} ({references.length})</button>
          <Button size="sm" pending={addReference.pending} disabled={!isTauri() && !referencePath.trim()} onclick={() => addReference.run()}>＋ {t('referenceFromFile')}</Button>
        </div>
        {#if !isTauri()}<label>{t('referencePath')}<input bind:value={referencePath} /></label>{/if}
        {#if mode === 'recording'}
          <div class="form-grid">
            <label>{t('recordingDate')}<input type="date" bind:value={recordingDate} onchange={() => { recordingId = String(dayRecords[0]?.id ?? ''); resetRange(); }} /></label>
            <label>{t('chooseRecording')}<select bind:value={recordingId} onchange={resetRange}>
              {#each dayRecords as r (r.id)}<option value={String(r.id)}>{siteTime(r, 'short')} · {fileName(r.path)}</option>{/each}</select></label>
          </div>
        {:else}
          <label>{t('referenceSource')}<select bind:value={referenceId} onchange={resetRange}>
            {#each references as row (row.id)}<option value={String(row.id)}>{fileName(row.path)}</option>{/each}</select></label>
        {/if}
        <details class="xc-panel"><summary>{t('xcFind')}</summary><XenoCantoSearch {species} onselect={selectReference} /></details>
        {#if source?.attribution}<p class="muted">XC{source.attribution.id} · {source.attribution.recordist} · {source.attribution.license}</p>{/if}
        {#if source}
          {#key `${playerKey}:${source.kind}:${source.id}`}
            {#if preview}
              <div class="listening"><strong>{t('listeningHit')} · {fileName(preview.path)} · {clipTime(preview.start_s)}–{clipTime(preview.end_s)}</strong>
                <Button size="sm" onclick={() => { preview = null; activeHit = null; playerKey++; }}>{t('backToReference')}</Button></div>
              <AudioRegion source={{ id: preview.recording_id, kind: 'recording', path: preview.path, seconds: preview.seconds }} bind:start={previewStart} bind:end={previewEnd} bind:status bind:this={player} compact />
            {:else}
              <AudioRegion {source} bind:start bind:end bind:status bind:this={player} />
            {/if}
          {/key}
        {:else if !loadError}<p class="muted"><Spinner size={12} /> {t('loading')}</p>{/if}
        <div class="actions clip-actions">
          <Button variant="primary" disabled={!species || !validRange || preview !== null || atLimit} onclick={addClip}>＋ {t('addQueryClip')}</Button>
          <Button pending={quick.pending} disabled={busyJob || !validRange || !validPeriod || preview !== null} onclick={() => quick.run()}>{t('quickSearch')}</Button>
          <span class="muted">{t('queryRangeHint')}</span>
        </div>
        {#if quick.error}<p class="error" role="alert">{t(quick.error)}</p>{/if}
      </section>
      <section class="panel step-card">
        <h2>{t('stepPeriod')}</h2><p class="muted">{t('searchPeriodHint')}</p>
        <div class="form-grid">
          <label>{t('searchStartDate')}<input type="date" bind:value={first} max={last || undefined} onchange={savePeriod} /></label>
          <label>{t('searchEndDate')}<input type="date" bind:value={last} min={first || undefined} onchange={savePeriod} /></label>
        </div>
        {#if !validPeriod}<p class="error">{t('search_period')}</p>{/if}
      </section>
      {#if result}
        <section class="panel" aria-label={t('quickResults')}>
          <div class="section-heading"><h2>{t('quickResults')} · {result.hits.length}</h2><Button size="sm" onclick={() => { result = null; preview = null; }}>{t('close')}</Button></div>
          <p class="muted">{t('searchedReference')}: {fileName(result.source.path)} · {clipTime(result.query.start_s)}–{clipTime(result.query.end_s)} · {t('searchCoverage')}: {result.searched_recordings} / {result.total_recordings} {t('files')} · {result.searched_segments} {t('analyzedSegments')}</p>
          {#if result.unsearched_recordings}<p class="notice">{t('partialSearch')}</p>{/if}
          <details><summary>{t('queryWindows')}</summary>{#each result.query.windows as window}<p class="muted">{clipTime(window.start_s)}–{clipTime(window.end_s)} · {t('zeroPadding')}: {window.padding_s.toFixed(2)} {t('seconds')}</p>{/each}</details>
          {#if !result.hits.length}<p class="empty">{t('noSimilarSounds')}</p>
          {:else}
            <div class="table-wrap hits">
              <table><thead><tr><th>{t('recording')}</th><th>{t('similarity')}</th><th>{t('segment')}</th></tr></thead><tbody>
                {#each result.hits as hit (hit.segment_id)}
                  {@const active = activeHit === hit.segment_id && status !== 'idle'}
                  <tr class="row-link" class:chosen={active} onclick={() => listen(hit)}>
                    <td>{fileName(hit.path)}<div class="muted">{siteTime(hit, 'short')}</div></td><td class="mono">{hit.similarity.toFixed(3)}</td>
                    <td class="nowrap"><button class="button sm" class:active aria-pressed={active} onclick={e => { e.stopPropagation(); listen(hit); }}>
                      {#if active && status === 'loading'}<Spinner size={12} />{:else if active}❚❚{:else}▶{/if} {clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td>
                  </tr>
                {/each}
              </tbody></table>
            </div>
          {/if}
        </section>
      {/if}
    </div>
    <aside class="stack">
      <section class="panel query-set">
        <h2>{t('stepQuerySet')}</h2>
        <p class="muted">{t('querySetHint')}</p>
        {#if !targets.length}<p class="empty">{t('addQueryClip')}</p>{/if}
        {#each targets as target, ti (target.species.id)}
          <div class="query-target">
            <strong>{commonName(target.species, app.locale)} · <i>{target.species.scientific_name}</i></strong>
            {#each target.clips as clip, ci}
              <div class="clip"><span class="muted">{fileName(clip.source.path)} · {clipTime(clip.start_s)}–{clipTime(clip.end_s)}</span>
                <button type="button" class="button sm ghost" aria-label={`${t('removeReference')} ${ci + 1}`} onclick={() => { targets[ti].clips.splice(ci, 1); targets = targets.filter(row => row.clips.length); saveDraft(); }}>×</button></div>
            {/each}
          </div>
        {/each}
        <Button variant="primary" size="lg" pending={saveAndSearch.pending} disabled={!clipCount || busyJob || !validPeriod} onclick={() => saveAndSearch.run()}>{t('saveAndSearch')}</Button>
        {#if saveAndSearch.pending}<p class="muted" role="status">{t('searchProgress')}</p>{/if}
        {#if saveAndSearch.error}<p class="error" role="alert">{t(saveAndSearch.error)}</p>{/if}
        {#if busyJob}<p class="notice">{t('job_busy')}</p>{/if}
      </section>
      <section class="panel" aria-label={t('savedSearches')}>
        <h2>{t('savedSearches')}</h2>
        <p class="muted">{t('savedSearchesIntro')}</p>
        {#if !runs.length}<p class="muted">{t('noSavedSearches')}</p>{/if}
        <div class="list" role="list" aria-label={t('searchHistory')}>
          {#each runs as run (run.id)}
            <div class="list-item" role="listitem">
              <span>#{run.id} · <strong>{commonName(run.species, app.locale)}</strong> <i class="muted">{run.species.scientific_name}</i></span>
              <Button size="sm" href={resolve(`/datasets/${datasetId}/search/${run.id}`)} aria-label={t('review')}>{t('review')} ›</Button>
            </div>
          {/each}
        </div>
      </section>
    </aside>
  </div>
</div>

<style>
  .source-tabs { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
  .xc-panel { margin: 12px 0; border: 1px solid rgb(var(--stone-300)); border-radius: 6px; padding: 10px 12px; }
  .xc-panel[open] summary { margin-bottom: 10px; }
  .listening { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; background: rgb(var(--primary-100)); padding: 8px 12px; font-size: 12px; border-radius: 6px; margin-bottom: 8px; }
  :global(.dark) .listening { background: rgb(var(--stone-200)); }
  .clip-actions { margin-top: 14px; }
  .hits { max-height: 380px; margin-top: 10px; }
  .nowrap { white-space: nowrap; }
  .query-set { position: sticky; top: 0; }
  .query-target { font-size: 12px; margin: 10px 0; padding: 8px 10px; border: 1px solid rgb(var(--stone-300)); border-radius: 6px; }
  .clip { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin-top: 4px; }
  .query-set :global(.button.lg) { width: 100%; margin-top: 12px; }
  .list-item span { min-width: 0; overflow-wrap: anywhere; }
</style>
