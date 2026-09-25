<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, datasetOf, siteTime } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import AudioRegion, { type AudioSource } from '$lib/AudioRegion.svelte';
  import SimilarityDistribution, { type Band } from '$lib/SimilarityDistribution.svelte';
  import ReferencePanel, { type ExampleRef } from '$lib/ReferencePanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Hit = { segment_id: number; recording_id: number; path: string; seconds: number; recorded_at: string; timezone: string;
    start_s: number; end_s: number; similarity: number; reference_index: number; verdict: string | null; note: string | null };
  type Sample = { id: number; low: number; high: number; population: number; count: number; seed: string };
  type Run = { related?:{id:number;species:Species}[]; id: number; created_at: string; hit_count: number; hits: Hit[]; sample_id: number | null; samples: Sample[]; distribution: Band[];
    snapshot: { species: Species; searched_recordings: number; total_recordings: number; searched_segments: number; unsearched_recordings: number;
      start_date?: string | null; end_date?: string | null; references: { source: AudioSource; query: { start_s: number; end_s: number } }[] } };
  const datasetId = $derived(Number(page.params.id)), runId = $derived(Number(page.params.run));
  const dataset = $derived(datasetOf(datasetId));
  let run = $state<Run | null>(null), sampleId = $state(''), loadError = $state('');
  const range=$derived.by(()=>{
    const query=new URL(page.url.hash.slice(1),page.url).searchParams,dates=new URLSearchParams();
    for(const key of ['start_date','end_date'] as const){const value=query.get(key)||run?.snapshot[key];if(value)dates.set(key,value);}
    return dates.toString();
  });
  let low = $state(0.5), high = $state(1), count = $state(20);
  let judging = $state<number | null>(null), index=$state(0), sampling=$state(false), heardExamples=$state<ExampleRef[]>();
  const currentHit=$derived(run?.hits[index]);
  const sample = $derived(run?.samples.find(s => String(s.id) === sampleId));
  const reviewed = $derived(run ? run.hits.filter(h => h.verdict).length : 0);
  async function load() {
    run = await api<Run>(`/search-runs/${runId}${sampleId ? `?sample_id=${sampleId}` : ''}`);
  }
  const draw = createTask(async () => {
    run = await api<Run>(`/search-runs/${runId}/samples`, { low, high, count }); sampleId = String(run!.sample_id); index=0;sampling=false;localStorage.setItem(`echoroo-review-${runId}`,sampleId);
  });
  const judge = createTask(async (hit: Hit, verdict: string) => {
    judging = hit.segment_id;
    try {
      await api(`/search-runs/${runId}/judgements`, { segment_id: hit.segment_id, verdict, note: hit.note || '', examples_snapshot: heardExamples });
      await load(); index=Math.min(index+1,run!.hits.length);
    } finally { judging = null; }
  }, { toast: true });
  $effect(() => {
    const id = runId;
    loadError = ''; run = null; sampleId = localStorage.getItem(`echoroo-review-${id}`)||'';
    api<Run>(`/search-runs/${id}${sampleId?`?sample_id=${sampleId}`:''}`).then(value => { if (id === runId) {run = value;const occupied=value.distribution.filter(b=>b.count);if(occupied.length){low=Number(occupied[0].low.toFixed(1));high=Number(occupied[occupied.length-1].high.toFixed(1));}const next=value.hits.findIndex(h=>!h.verdict);index=next<0?value.hits.length:next;} })
      .catch(e => { if (id === runId) loadError = e instanceof Error ? e.message : 'unknown_error'; });
  });
  // Search results feed the species page's search tab, where their reliability is checked.
  const speciesHref=$derived(run?`${resolve(`/datasets/${datasetId}/species/${run.snapshot.species.id}`)}?${range}&run_id=${runId}`:'');
</script>
<div class="page review-page">
  <div class="crumbs"><a href={speciesHref}>{t('wBackSpecies')}</a><span>{app.sites.find(s=>s.id===dataset?.site_id)?.name}</span></div>
  {#if loadError}<p class="error" role="alert">{t(loadError)}</p>
  {:else if !run}<p role="status">{t('loading')}</p>
  {:else}
    <h1>{commonName(run.snapshot.species,app.locale)}<i class="sci">{run.snapshot.species.scientific_name}</i></h1>
    {#if (run.related?.length??0)>1}<label>{t('uReviewSpecies')}<select value={String(runId)} onchange={e=>goto(resolve(`/datasets/${datasetId}/search/${e.currentTarget.value}`))}>{#each run.related??[] as other}<option value={String(other.id)}>{commonName(other.species,app.locale)} · {other.species.scientific_name}</option>{/each}</select></label>{/if}
    <p>{t('uReviewHint')}</p>
    {#if run.snapshot.unsearched_recordings}<p class="notice">{t('partialSearch')}</p>{/if}
    {#if sampling}<section class="panel"><h2>{t('uReviewOtherBand')}</h2>
      <SimilarityDistribution bands={run.distribution} hint={t('reviewDistributionHint')}/>
      <form class="form-grid" onsubmit={e=>{e.preventDefault();draw.run();}}>
        <label>{t('similarityLow')}<input type="number" min="-1" max="1" step="any" required bind:value={low}/></label>
        <label>{t('similarityHigh')}<input type="number" min="-1" max="1" step="any" required bind:value={high}/></label>
        <label>{t('sampleCount')}<input type="number" min="1" max="200" required bind:value={count}/></label>
        <Button type="submit" variant="primary" pending={draw.pending} disabled={low>high}>{t('uReviewStart')}</Button>
      </form><Button onclick={()=>sampling=false}>{t('uBackSounds')}</Button>
      {#if draw.error}<p class="error" role="alert">{t(draw.error)}</p>{/if}
    </section>
    {:else}<div class="with-examples"><section class="panel"><div class="phase-progress"><h2>{t('vQuestionNamed').replace('{name}',commonName(run.snapshot.species,app.locale)||run.snapshot.species.scientific_name)}</h2><p>{Math.min(index+1,run.hits.length)} / {run.hits.length} · {reviewed} {t('wAnswered')}</p></div>
      <p class="muted">{t('uReviewWhy')}</p>
      {#if currentHit}
        <p>{siteTime(currentHit,'short')}</p>
        <!-- Same listening parts as the validation screen; each sound starts from the whole clip. -->
        {#key currentHit.segment_id}<AudioRegion source={{id:currentHit.recording_id,kind:'recording',path:currentHit.path,seconds:currentHit.seconds}} start={currentHit.start_s} end={currentHit.end_s} fixedRange zoomable storageKey={`echoroo-review:${runId}:${currentHit.segment_id}:view`}/>{/key}
        <div class="actions answers"><Button disabled={index===0||judge.pending} onclick={()=>index--}>‹ {t('uPreviousSound')}</Button>
          <div class="verdicts" role="group" aria-label={t('uReviewPrompt')}>{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
          <Button size="lg" active={currentHit.verdict===verdict} aria-pressed={currentHit.verdict===verdict} disabled={judge.pending} onclick={()=>judge.run(currentHit,verdict)}>{currentHit.verdict===verdict?'✓ ':''}{t(label)}</Button>{/each}</div>
          <Button disabled={index>=run.hits.length-1||judge.pending} onclick={()=>index++}>{t('uNextSound')} ›</Button>
          <p class="muted unknown-note">{t('vUnknownNote')}</p></div>
      {:else}<p>{t('uReviewDone')}</p>{/if}
      {#if judge.error}<p class="error" role="alert">{t(judge.error)}</p>{/if}
    </section>
    <ReferencePanel species={run.snapshot.species} {datasetId} {runId} sampleId={run.sample_id} onexamples={list=>heardExamples=list}
      practice={`${resolve('/quiz')}?species=${run.snapshot.species.id}&return=${encodeURIComponent(`/datasets/${datasetId}/search/${runId}`)}`}/></div>
    <div class="actions"><Button variant={currentHit?'default':'primary'} href={speciesHref}>{t('rSearchResults')}</Button><Button onclick={()=>sampling=true}>{t('uReviewOtherBand')}</Button></div>
    <p class="muted">{t('uSavedAlways')}</p>{/if}
  {/if}
</div>
<style>.review-page{max-width:1280px!important}.with-examples{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,380px);gap:16px;align-items:start}.with-examples>:global(.examples){position:sticky;top:16px}@media(max-width:950px){.with-examples{grid-template-columns:minmax(0,1fr)}.with-examples>:global(.examples){position:static}}p{margin:16px 0}.phase-progress{display:flex;align-items:center;justify-content:space-between;gap:12px}.answers{position:fixed;bottom:0;left:0;right:0;z-index:3;justify-content:space-between;align-items:center;row-gap:4px;padding:10px max(24px,calc((100vw - 1280px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));margin:0}.answers .verdicts{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}.unknown-note{flex-basis:100%;text-align:center;margin:0!important;font-size:14px}.review-page{padding-bottom:130px}@media(max-width:700px){.answers{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:8px 12px}.answers .verdicts{grid-column:1/-1;order:-1;flex-wrap:nowrap;gap:6px}.answers .verdicts :global(.button){flex:1;padding:8px;font-size:16px;min-height:48px}.unknown-note{grid-column:1/-1}.review-page{padding-bottom:220px}}form{margin:20px 0}</style>
