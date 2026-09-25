<script lang="ts">
  import { goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, datasetOf, siteTime } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import AudioRegion, { type AudioSource } from '$lib/AudioRegion.svelte';
  import SimilarityDistribution, { type Band } from '$lib/SimilarityDistribution.svelte';
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
  let judging = $state<number | null>(null), index=$state(0), sampling=$state(false);
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
      await api(`/search-runs/${runId}/judgements`, { segment_id: hit.segment_id, verdict, note: hit.note || '' });
      await load(); index=Math.min(index+1,run!.hits.length);
    } finally { judging = null; }
  }, { toast: true });
  $effect(() => {
    const id = runId;
    loadError = ''; run = null; sampleId = localStorage.getItem(`echoroo-review-${id}`)||'';
    api<Run>(`/search-runs/${id}${sampleId?`?sample_id=${sampleId}`:''}`).then(value => { if (id === runId) {run = value;const occupied=value.distribution.filter(b=>b.count);if(occupied.length){low=Number(occupied[0].low.toFixed(1));high=Number(occupied[occupied.length-1].high.toFixed(1));}const next=value.hits.findIndex(h=>!h.verdict);index=next<0?value.hits.length:next;} })
      .catch(e => { if (id === runId) loadError = e instanceof Error ? e.message : 'unknown_error'; });
  });
</script>
<div class="page review-page">
  <div class="crumbs"><a href={resolve(`/datasets/${datasetId}/search`)}>{t('backToSearch')}</a><span>{app.sites.find(s=>s.id===dataset?.site_id)?.name}</span></div>
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
    {:else}<section class="panel"><h2>{t('uReviewPrompt')}</h2>
      <p>{Math.min(index+1,run.hits.length)} / {run.hits.length} · {t('uSoundNumber')}</p>
      <progress value={reviewed} max={run.hits.length}></progress>
      {#if currentHit}
        <p>{siteTime(currentHit,'short')}</p>
        {#key currentHit.segment_id}<AudioRegion source={{id:currentHit.recording_id,kind:'recording',path:currentHit.path,seconds:currentHit.seconds}} start={currentHit.start_s} end={currentHit.end_s} fixedRange/>{/key}
        <div class="actions answers">{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
          <Button size="lg" active={currentHit.verdict===verdict} aria-pressed={currentHit.verdict===verdict} pending={judge.pending} onclick={()=>judge.run(currentHit,verdict)}>{currentHit.verdict===verdict?'✓ ':''}{t(label)}</Button>{/each}</div>
      {:else}<p>{t('uReviewDone')}</p>{/if}
      {#if judge.error}<p class="error" role="alert">{t(judge.error)}</p>{/if}
      <div class="actions between"><Button disabled={index===0||judge.pending} onclick={()=>index--}>‹ {t('uPreviousSound')}</Button><Button disabled={index>=run.hits.length-1||judge.pending} onclick={()=>index++}>{t('uNextSound')} ›</Button></div>
    </section>
    <div class="actions"><Button href={`${resolve(`/datasets/${datasetId}/species/${run.snapshot.species.id}`)}?${range}&run_id=${runId}`}>{t('rSearchResults')}</Button><Button onclick={()=>sampling=true}>{t('uReviewOtherBand')}</Button></div>
    <p class="muted">{t('uSavedAlways')}</p>{/if}
  {/if}
</div>
<style>.review-page{max-width:820px!important}p{margin:16px 0}.answers{margin:24px 0}.between{justify-content:space-between;margin-top:28px}form{margin:20px 0}</style>
