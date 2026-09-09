<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api';
  import { commonName, type Species } from './species';
  import { clipTime, type AudioSource } from './AudioRegion.svelte';
  import SimilarityDistribution, { type Band } from './SimilarityDistribution.svelte';
  export type Hit = { segment_id: number; recording_id: number; path: string; seconds: number; recorded_at: string; timezone: string;
    start_s: number; end_s: number; similarity: number; reference_index: number; verdict: string | null; note: string | null };
  type RunSummary = { id: number; species: Species; created_at: string };
  type Sample = { id: number; low: number; high: number; population: number; count: number; seed: string };
  export type Run = { id: number; created_at: string; hit_count: number; hits: Hit[]; sample_id: number | null; samples: Sample[];
    distribution: Band[];
    snapshot: { species: Species; searched_recordings: number; total_recordings: number; searched_segments: number; unsearched_recordings: number;
      start_date?: string | null; end_date?: string | null;
      references: { source: AudioSource; query: { start_s: number; end_s: number } }[] } };
  let { datasetId, locale, t, onlisten }: { datasetId: number; locale: string; t: (key: string) => string; onlisten: (hit: Hit) => Promise<void> } = $props();
  let rows = $state<RunSummary[]>([]), run = $state<Run | null>(null), chosen = $state(''), sampleId = $state('');
  let low = $state(0.5), high = $state(1), count = $state(20), busy = $state(false), error = $state('');
  const name = (path: string) => path.split(/[\\/]/).pop();
  const date = (hit: Hit) => new Intl.DateTimeFormat(locale, { dateStyle: 'short', timeStyle: 'short', timeZone: hit.timezone }).format(new Date(hit.recorded_at));
  const sample = $derived(run?.samples.find(s => String(s.id) === sampleId));
  async function action(fn: () => Promise<void>) {
    busy = true; error = '';
    try { await fn(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
  async function refresh() { rows = await api<RunSummary[]>(`/search-runs?dataset_id=${datasetId}`); }
  async function load() {
    if (!chosen) { run = null; return; }
    run = await api<Run>(`/search-runs/${chosen}${sampleId ? `?sample_id=${sampleId}` : ''}`);
  }
  export async function showRuns(runs: Run[]) {
    await refresh(); run = runs[0]; chosen = String(run.id); sampleId = '';
  }
  async function draw() {
    run = await api<Run>(`/search-runs/${chosen}/samples`, { low, high, count }); sampleId = String(run.sample_id);
  }
  async function judge(hit: Hit, verdict: string) {
    await api(`/search-runs/${chosen}/judgements`, { segment_id: hit.segment_id, verdict, note: hit.note || '' });
    await load();
  }
  onMount(() => { void action(refresh); });
</script>

<section class="review" aria-label={t('savedSearches')}>
  <h3>{t('savedSearches')}</h3>
  <label>{t('searchHistory')}<select bind:value={chosen} disabled={busy} onchange={() => { sampleId = ''; void action(load); }}>
    <option value="">{t('chooseSearch')}</option>{#each rows as row}<option value={String(row.id)}>#{row.id} · {commonName(row.species, locale)} · {row.species.scientific_name}</option>{/each}</select></label>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if run}
    <h3 class="target">{commonName(run.snapshot.species, locale)} · <i>{run.snapshot.species.scientific_name}</i></h3>
    <p class="muted">{t('searchRecordingDates')}: {run.snapshot.start_date ?? t('noDateLimit')}–{run.snapshot.end_date ?? t('noDateLimit')}</p>
    <p class="muted">{t('searchCoverage')}: {run.snapshot.searched_recordings} / {run.snapshot.total_recordings} {t('files')} · {run.hit_count} {t('searchableSegments')}</p>
    {#if run.snapshot.unsearched_recordings}<p class="notice">{t('partialSearch')}</p>{/if}
    <details><summary>{t('savedReferences')} ({run.snapshot.references.length})</summary>
      {#each run.snapshot.references as ref, i}<p class="muted">{i + 1}. {name(ref.source.path)} · {clipTime(ref.query.start_s)}–{clipTime(ref.query.end_s)}</p>{/each}</details>
    <p class="muted">{t('similarityHint')}</p>
    <details class="distribution" open><summary>{t('similarityDistribution')}</summary>
      <SimilarityDistribution bands={run.distribution} {t} hint={t('reviewDistributionHint')}/></details>
    <form class="sampling" onsubmit={e => { e.preventDefault(); void action(draw); }}>
      <label>{t('similarityLow')}<input type="number" min="-1" max="1" step="any" required bind:value={low} /></label>
      <label>{t('similarityHigh')}<input type="number" min="-1" max="1" step="any" required bind:value={high} /></label>
      <label>{t('sampleCount')}<input type="number" min="1" max="200" step="1" required bind:value={count} /></label>
      <button disabled={busy || low > high}>{t('drawSample')}</button>
    </form>
    <label>{t('reviewSet')}<select bind:value={sampleId} disabled={busy} onchange={() => action(load)}>
      <option value="">{t('topMatches')}</option>{#each run.samples as s}<option value={String(s.id)}>#{s.id} · {s.low}–{s.high} · {s.count} / {s.population}</option>{/each}</select></label>
    {#if sample}<p class="muted">{t('samplePopulation')}: {sample.count} / {sample.population} · {t('randomSampleHint')}</p>{/if}
    <p class="notice">{t('reviewHint')}</p>
    <div class="hit-list"><table><thead><tr><th>{t('recording')}</th><th>{t('similarity')}</th><th>{t('segment')}</th><th>{t('judgement')}</th></tr></thead><tbody>
      {#each run.hits as hit}<tr><td>{name(hit.path)}<div class="muted">{date(hit)}</div></td>
        <td>{hit.similarity.toFixed(3)}<div class="muted">{t('winningReference')} {hit.reference_index + 1}</div></td>
        <td><button class="small" onclick={() => onlisten(hit)}>{clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td>
        <td><div class="verdicts">{#each ['target', 'other', 'unknown'] as verdict}<button class="small" class:chosen={hit.verdict === verdict} aria-pressed={hit.verdict === verdict}
          disabled={busy} onclick={() => action(() => judge(hit, verdict))}>{t(`verdict_${verdict}`)}</button>{/each}</div>
          <span class="muted">{hit.verdict ? t('judgementSaved') : t('unreviewed')}</span>
        </td></tr>{:else}<tr><td colspan="4">{t('noSimilarSounds')}</td></tr>{/each}
    </tbody></table></div>
  {/if}
</section>

<style>
  .review { border-top: 1px solid rgb(var(--stone-300)); margin-top: 20px; padding-top: 16px; }
  .target { margin-top: 16px; }
  .sampling { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; margin: 14px 0; }
  .sampling input { width: 100px; }
  .hit-list { max-height: 420px; overflow: auto; margin-top: 12px; }
  .verdicts { display: flex; flex-wrap: wrap; gap: 4px; }
  details { font-size: 12px; margin: 10px 0; }
  .distribution { max-height: 300px; overflow: auto; }
</style>
