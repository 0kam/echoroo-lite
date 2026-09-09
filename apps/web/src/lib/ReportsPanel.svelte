<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api';
  import { commonName, type Species } from './species';
  import type { Report, ReportOptions } from './reports';
  import ReportView from './ReportView.svelte';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import SimilarityDistribution from './SimilarityDistribution.svelte';
  let { datasetId, locale, t }: { datasetId: number; locale: string; t: (key: string) => string } = $props();
  type History = {id: number; kind: string; species: Species | null; start_date: string; end_date: string};
  let options = $state<ReportOptions | null>(null), runs = $state<{ id: number; species: Species }[]>([]), history = $state<History[]>([]);
  let kind = $state<'detection'|'search'>('detection'), model = $state(''), runId = $state(''), historyId = $state('');
  let first = $state(''), last = $state(''), threshold = $state(.1), similarity = $state(.5), useThreshold = $state(false);
  let species = $state<Species | null>(null), report = $state<Report | null>(null), busy = $state(false), error = $state('');
  async function action(fn: () => Promise<void>) {
    busy = true; error = '';
    try { await fn(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
  async function refreshHistory() { history = await api<History[]>(`/reports?dataset_id=${datasetId}`); }
  function restore(loaded: Report) {
    report=loaded; kind=loaded.kind; first=loaded.start_date; last=loaded.end_date; species=loaded.species;
    if(loaded.kind==='detection') { model=`${loaded.engine}:${loaded.model_sha256}`; threshold=loaded.threshold!; }
    else { runId=String(loaded.run_id); useThreshold=loaded.threshold!==null; similarity=loaded.threshold ?? .5; }
  }
  async function create(taxon = species, baseReportId: number | null = null) {
    if (kind==='detection') {
      const [engine, model_sha256] = model.split(':');
      report = await api<Report>('/reports/detection', { dataset_id: datasetId, engine, model_sha256,
        threshold, start_date:first, end_date:last, species_id:taxon?.id ?? null, base_report_id:baseReportId }); species = taxon;
    } else {
      report = await api<Report>('/reports/search', { run_id: Number(runId), start_date:first, end_date:last, threshold: useThreshold ? similarity : null });
    }
    historyId = String(report.id); await refreshHistory();
  }
  onMount(() => { void action(async () => {
    const [loaded, searches] = await Promise.all([api<ReportOptions>(`/reports/options?dataset_id=${datasetId}`), api<{id:number;species:Species}[]>(`/search-runs?dataset_id=${datasetId}`)]);
    options=loaded; runs=searches; last=loaded.last_date;
    first = [loaded.first_date, new Date(Date.parse(last)-365*86400000).toISOString().slice(0,10)].sort().at(-1)!;
    if(loaded.models.length) model=`${loaded.models[0].engine}:${loaded.models[0].model_sha256}`;
    if(searches.length) runId=String(searches[0].id);
    await refreshHistory();
  }); });
</script>

<section class="panel">
  <h2>{t('reports')}</h2>
  <div class="report-tabs"><button class:chosen={kind==='detection'} aria-pressed={kind==='detection'} disabled={busy} onclick={() => { kind='detection'; report=null; historyId=''; }}>{t('detectionReport')}</button>
    <button class:chosen={kind==='search'} aria-pressed={kind==='search'} disabled={busy} onclick={() => { kind='search'; report=null; historyId=''; }}>{t('searchReport')}</button></div>
  <p class="muted">{t(kind==='detection' ? 'detectionReportPurpose' : 'searchReportPurpose')}</p>
  <form onsubmit={e => { e.preventDefault(); void action(() => create()); }}>
    <div class="form-grid">
      <label>{t('reportFrom')}<input type="date" bind:value={first} required disabled={busy}/></label>
      <label>{t('reportTo')}<input type="date" bind:value={last} required disabled={busy}/></label>
      {#if kind==='detection'}<label>{t('model')}<select bind:value={model} disabled={busy}>{#each options?.models ?? [] as m}<option value={`${m.engine}:${m.model_sha256}`}>{m.engine==='birdnet'?'BirdNET':'Perch'} · {m.model_sha256.slice(0,12)} · {m.recordings} {t('files')}</option>{/each}</select></label>
        <label>{t('threshold')}<input type="number" min="0" max="1" step="any" required bind:value={threshold} disabled={busy}/></label>
      {:else}<label>{t('searchHistory')}<select bind:value={runId} disabled={busy}>{#each runs as run}<option value={String(run.id)}>#{run.id} · {commonName(run.species,locale)} · {run.species.scientific_name}</option>{/each}</select></label>
        <label>{t('threshold')}<input type="number" min="-1" max="1" step="any" required={useThreshold} bind:value={similarity} disabled={busy || !useThreshold}/></label>
      {/if}
    </div>
    {#if kind==='search'}<label class="checkbox"><input type="checkbox" bind:checked={useThreshold} disabled={busy}/>{t('applyDisplayThreshold')}</label>{/if}
    <button class="primary" disabled={busy || !options || (kind==='detection' ? !model : !runId)}>{t(busy ? 'creatingReport' : 'createReport')}</button>
  </form>
  {#if kind==='detection'}<details class="specific-species"><summary>{t('specifyReportSpecies')}</summary>
    <SpeciesPicker bind:value={species} {locale} {t} disabled={busy}/><button class="small" disabled={busy || !species || !model} onclick={() => action(() => create())}>{t('reportThisSpecies')}</button>
    {#if species}<button class="small" disabled={busy} onclick={() => action(() => create(null))}>{t('reportAllSpecies')}</button>{/if}</details>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if busy}<p class="muted" role="status">{t('reportProgress')}</p>{/if}
  {#if history.length}<label class="history">{t('reportHistory')}<select bind:value={historyId} disabled={busy} onchange={() => action(async () => {
    if(historyId) restore(await api<Report>(`/reports/${historyId}`));
  })}><option value="">{t('chooseReport')}</option>{#each history as row}<option value={String(row.id)}>#{row.id} · {t(row.kind==='search'?'searchReport':'detectionReport')} · {row.species ? `${commonName(row.species,locale)} · ${row.species.scientific_name}` : t('reportAllSpecies')} · {row.start_date}–{row.end_date}</option>{/each}</select></label>{/if}
  {#if report?.kind==='search' && report.distribution}
    <details class="distribution"><summary>{t('similarityDistribution')}</summary>
      <SimilarityDistribution bands={report.distribution} {t} hint={t('distributionHint')}/></details>
    {#if report.threshold_suggestion}<p class="muted">{t('suggestedDisplayThreshold')}: {report.threshold_suggestion.threshold.toFixed(3)} · {t('thresholdSuggestionHint')}</p>
      <button class="small" disabled={busy} onclick={() => action(async () => {
        similarity=report!.threshold_suggestion!.threshold; useThreshold=true; runId=String(report!.run_id); first=report!.start_date; last=report!.end_date; await create();
      })}>{t('useSuggestedThreshold')}</button>
    {:else}<p class="muted">{t('noThresholdSuggestion')}</p>{/if}
  {/if}
  {#if report}{#key report.id}<ReportView {report} {locale} {t} onspecies={taxon => { if(!busy) void action(async () => { const id=report!.id; restore(report!); await create(taxon,id); }); }}/>{/key}{/if}
</section>

<style>
  .report-tabs { display: flex; gap: 8px; margin: 14px 0; }
  .checkbox { display: flex; align-items: center; gap: 8px; margin-bottom: 14px; }
  .checkbox input { width: 18px; }
  .specific-species { margin: 15px 0; }
  .specific-species button { margin-top: 10px; }
  .history { margin: 20px 0; }
  .distribution { margin: 14px 0; max-height: 300px; overflow: auto; }
</style>
