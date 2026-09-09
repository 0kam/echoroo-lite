<script lang="ts">
  import { resolve } from '$app/paths';
  import { tick } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, datasetOf, fileName, siteTime } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import AudioRegion, { clipTime, type AudioSource, type PlayerStatus } from '$lib/AudioRegion.svelte';
  import SimilarityDistribution, { type Band } from '$lib/SimilarityDistribution.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type Hit = { segment_id: number; recording_id: number; path: string; seconds: number; recorded_at: string; timezone: string;
    start_s: number; end_s: number; similarity: number; reference_index: number; verdict: string | null; note: string | null };
  type Sample = { id: number; low: number; high: number; population: number; count: number; seed: string };
  type Run = { id: number; created_at: string; hit_count: number; hits: Hit[]; sample_id: number | null; samples: Sample[]; distribution: Band[];
    snapshot: { species: Species; searched_recordings: number; total_recordings: number; searched_segments: number; unsearched_recordings: number;
      start_date?: string | null; end_date?: string | null; references: { source: AudioSource; query: { start_s: number; end_s: number } }[] } };
  const datasetId = $derived(Number(page.params.id)), runId = $derived(Number(page.params.run));
  const dataset = $derived(datasetOf(datasetId));
  let run = $state<Run | null>(null), sampleId = $state(''), loadError = $state('');
  let low = $state(0.5), high = $state(1), count = $state(20);
  let clip = $state<Hit | null>(null), start = $state(0), end = $state(5), status = $state<PlayerStatus>('idle'), playerKey = $state(0);
  let player = $state<{ play: (from?: number, to?: number) => Promise<void>; stop: () => void }>(null!);
  let judging = $state<number | null>(null);
  const sample = $derived(run?.samples.find(s => String(s.id) === sampleId));
  const reviewed = $derived(run ? run.hits.filter(h => h.verdict).length : 0);
  async function load() {
    run = await api<Run>(`/search-runs/${runId}${sampleId ? `?sample_id=${sampleId}` : ''}`);
  }
  const reload = createTask(load, { toast: true });
  const draw = createTask(async () => {
    run = await api<Run>(`/search-runs/${runId}/samples`, { low, high, count }); sampleId = String(run!.sample_id);
  });
  const judge = createTask(async (hit: Hit, verdict: string) => {
    judging = hit.segment_id;
    try {
      await api(`/search-runs/${runId}/judgements`, { segment_id: hit.segment_id, verdict, note: hit.note || '' });
      await load(); notify('judgementSaved', 'success');
    } finally { judging = null; }
  }, { toast: true });
  async function listen(hit: Hit) {
    if (clip?.segment_id === hit.segment_id && status !== 'idle') { player.stop(); return; }
    clip = hit; start = hit.start_s; end = hit.end_s; playerKey++;
    await tick(); await player.play(hit.start_s, hit.end_s);
  }
  $effect(() => {
    const id = runId;
    loadError = ''; run = null; sampleId = '';
    api<Run>(`/search-runs/${id}`).then(value => { if (id === runId) run = value; })
      .catch(e => { if (id === runId) loadError = e instanceof Error ? e.message : 'unknown_error'; });
  });
</script>

<div class="page">
  <div class="page-header">
    <div>
      <div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span><a href={resolve(`/datasets/${datasetId}`)}>{dataset?.name}</a><span>›</span><a href={resolve(`/datasets/${datasetId}/search`)}>{t('navSearch')}</a><span>›</span></div>
      {#if run}<h1>#{run.id} · {commonName(run.snapshot.species, app.locale)} <i class="sci">{run.snapshot.species.scientific_name}</i></h1>
        <p>{t('searchRecordingDates')}: {run.snapshot.start_date ?? t('noDateLimit')}–{run.snapshot.end_date ?? t('noDateLimit')} · {t('searchCoverage')}: {run.snapshot.searched_recordings} / {run.snapshot.total_recordings} {t('files')} · {run.hit_count} {t('searchableSegments')}</p>
      {:else}<h1>{t('savedSearches')}</h1>{/if}
    </div>
    <div class="actions"><Button href={resolve(`/datasets/${datasetId}/search`)}>‹ {t('backToSearch')}</Button><Button href={resolve(`/datasets/${datasetId}/reports`)}>{t('reports')} ›</Button></div>
  </div>
  {#if loadError}<div class="panel empty"><p class="error">{t(loadError === 'not_found' ? 'runNotFound' : loadError)}</p></div>
  {:else if !run}<p class="muted"><Spinner /> {t('loading')}</p>
  {:else}
    {#if run.snapshot.unsearched_recordings}<p class="notice">{t('partialSearch')}</p>{/if}
    <div class="two-col">
      <div class="stack">
        <section class="panel">
          <h2>{t('similarityDistribution')}</h2>
          <details><summary>{t('savedReferences')} ({run.snapshot.references.length})</summary>
            {#each run.snapshot.references as ref, i}<p class="muted">{i + 1}. {fileName(ref.source.path)} · {clipTime(ref.query.start_s)}–{clipTime(ref.query.end_s)}</p>{/each}</details>
          <p class="muted">{t('similarityHint')}</p>
          <SimilarityDistribution bands={run.distribution} hint={t('reviewDistributionHint')} />
        </section>
        <section class="panel">
          <h2>{t('drawSample')}</h2>
          <p class="muted">{t('randomSampleHint')}</p>
          <form class="sampling" onsubmit={e => { e.preventDefault(); draw.run(); }}>
            <label>{t('similarityLow')}<input type="number" min="-1" max="1" step="any" required bind:value={low} /></label>
            <label>{t('similarityHigh')}<input type="number" min="-1" max="1" step="any" required bind:value={high} /></label>
            <label>{t('sampleCount')}<input type="number" min="1" max="200" step="1" required bind:value={count} /></label>
            <Button type="submit" variant="primary" pending={draw.pending} disabled={low > high}>{t('drawSample')}</Button>
          </form>
          {#if draw.error}<p class="error" role="alert">{t(draw.error)}</p>{/if}
        </section>
      </div>
      <aside class="panel sticky">
        {#if clip}
          <h2>{t('listeningHit')}</h2>
          <p class="muted">{fileName(clip.path)} · {siteTime(clip, 'short')} · {clipTime(clip.start_s)}–{clipTime(clip.end_s)}</p>
          {#key `${playerKey}:${clip.recording_id}`}
            <AudioRegion source={{ id: clip.recording_id, kind: 'recording', path: clip.path, seconds: clip.seconds }} bind:start bind:end bind:status bind:this={player} compact />
          {/key}
        {:else}<h2>{t('listeningHit')}</h2><p class="empty">{t('reviewHint')}</p>{/if}
      </aside>
    </div>
    <section class="panel">
      <div class="section-heading">
        <div><h2>{t('reviewSet')}</h2><p class="muted">{reviewed} / {run.hits.length} · {t('judgementSaved')}</p></div>
        <label class="set">{t('reviewSet')}<select bind:value={sampleId} disabled={reload.pending} onchange={() => reload.run()}>
          <option value="">{t('topMatches')}</option>{#each run.samples as s}<option value={String(s.id)}>#{s.id} · {s.low}–{s.high} · {s.count} / {s.population}</option>{/each}</select></label>
      </div>
      {#if sample}<p class="muted">{t('samplePopulation')}: {sample.count} / {sample.population}</p>{/if}
      <p class="notice">{t('reviewHint')}</p>
      <div class="table-wrap hits">
        <table>
          <thead><tr><th>{t('recording')}</th><th>{t('similarity')}</th><th>{t('segment')}</th><th>{t('judgement')}</th></tr></thead>
          <tbody>
            {#each run.hits as hit (hit.segment_id)}
              {@const active = clip?.segment_id === hit.segment_id && status !== 'idle'}
              <tr class:chosen={active}>
                <td>{fileName(hit.path)}<div class="muted">{siteTime(hit, 'short')}</div></td>
                <td class="mono">{hit.similarity.toFixed(3)}<div class="muted">{t('winningReference')} {hit.reference_index + 1}</div></td>
                <td class="nowrap"><button class="button sm" class:active aria-pressed={active} onclick={() => listen(hit)}>
                  {#if active && status === 'loading'}<Spinner size={12} />{:else if active}❚❚{:else}▶{/if} {clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td>
                <td><div class="verdicts">
                  {#each ['target', 'other', 'unknown'] as verdict}
                    <Button size="sm" active={hit.verdict === verdict} aria-pressed={hit.verdict === verdict} pending={judging === hit.segment_id && judge.pending && hit.verdict !== verdict}
                      disabled={judge.pending} onclick={() => judge.run(hit, verdict)}>{t(`verdict_${verdict}`)}</Button>
                  {/each}
                  <span class="muted">{hit.verdict ? t('judgementSaved') : t('unreviewed')}</span></div></td>
              </tr>
            {:else}<tr><td colspan="4" class="muted">{t('noSimilarSounds')}</td></tr>{/each}
          </tbody>
        </table>
      </div>
    </section>
  {/if}
</div>

<style>
  .sci { font-weight: 400; font-size: 15px; color: rgb(var(--stone-600)); }
  .sampling { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 12px; margin-top: 10px; }
  .sampling label { width: 120px; }
  .sticky { position: sticky; top: 0; }
  .set { width: 260px; }
  .hits { max-height: 520px; margin-top: 10px; }
  .nowrap { white-space: nowrap; }
  .verdicts { display: flex; flex-wrap: wrap; align-items: center; gap: 4px; }
</style>
