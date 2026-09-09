<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, datasetOf } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import type { Report, ReportOptions } from '$lib/reports';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type History = { id: number; kind: 'detection' | 'search'; species: Species | null; start_date: string; end_date: string };
  type RunSummary = { id: number; species: Species };
  const datasetId = $derived(Number(page.params.id));
  const dataset = $derived(datasetOf(datasetId));
  let options = $state<ReportOptions | null>(null), runs = $state<RunSummary[]>([]), history = $state<History[]>([]);
  let kind = $state<'detection' | 'search'>('detection'), model = $state(''), runId = $state('');
  let first = $state(''), last = $state(''), threshold = $state(.1), similarity = $state(.5), useThreshold = $state(false);
  let species = $state<Species | null>(null), loadError = $state('');
  const create = createTask(async (taxon: Species | null = species) => {
    let report: Report;
    if (kind === 'detection') {
      const [engine, model_sha256] = model.split(':');
      report = await api<Report>('/reports/detection', { dataset_id: datasetId, engine, model_sha256, threshold,
        start_date: first, end_date: last, species_id: taxon?.id ?? null, base_report_id: null });
    } else {
      report = await api<Report>('/reports/search', { run_id: Number(runId), start_date: first, end_date: last,
        threshold: useThreshold ? similarity : null });
    }
    notify('reportCreated');
    await goto(resolve(`/datasets/${datasetId}/reports/${report.id}`));
  }, { toast: true });
  onMount(() => { void (async () => {
    try {
      const [loaded, searches, saved] = await Promise.all([
        api<ReportOptions>(`/reports/options?dataset_id=${datasetId}`),
        api<RunSummary[]>(`/search-runs?dataset_id=${datasetId}`),
        api<History[]>(`/reports?dataset_id=${datasetId}`),
      ]);
      options = loaded; runs = searches; history = saved; last = loaded.last_date;
      first = [loaded.first_date, new Date(Date.parse(last) - 365 * 86400000).toISOString().slice(0, 10)].sort().at(-1)!;
      if (loaded.models.length) model = `${loaded.models[0].engine}:${loaded.models[0].model_sha256}`;
      if (searches.length) runId = String(searches[0].id);
    } catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
  })(); });
</script>

<div class="page">
  <div class="page-header">
    <div><div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span><a href={resolve(`/datasets/${datasetId}`)}>{dataset?.name}</a><span>›</span></div>
      <h1>{t('reports')}</h1><p>{t('reportsIntro')}</p></div>
  </div>
  {#if loadError}<div class="panel"><p class="error" role="alert">{t(loadError)}</p></div>
  {:else if !options}<p class="muted"><Spinner /> {t('loading')}</p>
  {:else}
    <div class="two-col">
      <section class="panel">
        <h2>{t('newReport')}</h2>
        <div class="report-tabs">
          <Button active={kind === 'detection'} aria-pressed={kind === 'detection'} disabled={create.pending} onclick={() => kind = 'detection'}>{t('detectionReport')}</Button>
          <Button active={kind === 'search'} aria-pressed={kind === 'search'} disabled={create.pending} onclick={() => kind = 'search'}>{t('searchReport')}</Button>
        </div>
        <p class="muted purpose">{t(kind === 'detection' ? 'detectionReportPurpose' : 'searchReportPurpose')}</p>
        <form onsubmit={event => { event.preventDefault(); create.run(); }}>
          <div class="form-grid">
            <label>{t('reportFrom')}<input type="date" bind:value={first} required disabled={create.pending} /></label>
            <label>{t('reportTo')}<input type="date" bind:value={last} required disabled={create.pending} /></label>
            {#if kind === 'detection'}
              <label>{t('model')}<select bind:value={model} disabled={create.pending}>{#each options.models as item}<option value={`${item.engine}:${item.model_sha256}`}>{item.engine === 'birdnet' ? 'BirdNET' : 'Perch'} · {item.model_sha256.slice(0, 12)} · {item.recordings} {t('files')}</option>{/each}</select></label>
              <label>{t('threshold')}<input type="number" min="0" max="1" step="any" required bind:value={threshold} disabled={create.pending} /></label>
            {:else}
              <label>{t('searchHistory')}<select bind:value={runId} disabled={create.pending}>{#each runs as run}<option value={String(run.id)}>#{run.id} · {commonName(run.species, app.locale)} · {run.species.scientific_name}</option>{/each}</select></label>
              <label>{t('threshold')}<input type="number" min="-1" max="1" step="any" required={useThreshold} bind:value={similarity} disabled={create.pending || !useThreshold} /></label>
            {/if}
          </div>
          {#if kind === 'search'}<label class="checkbox option"><input type="checkbox" bind:checked={useThreshold} disabled={create.pending} />{t('applyDisplayThreshold')}</label>{/if}
          <Button type="submit" variant="primary" pending={create.pending} disabled={kind === 'detection' ? !model : !runId}>{t('createReport')}</Button>
        </form>
        {#if kind === 'detection'}
          <details class="specific-species"><summary>{t('specifyReportSpecies')}</summary>
            <SpeciesPicker bind:value={species} disabled={create.pending} />
            <div class="actions"><Button size="sm" pending={create.pending} disabled={!species || !model} onclick={() => create.run(species)}>{t('reportThisSpecies')}</Button>
              {#if species}<Button size="sm" disabled={create.pending} onclick={() => create.run(null)}>{t('reportAllSpecies')}</Button>{/if}</div>
          </details>
        {/if}
        {#if create.pending}<p class="muted" role="status">{t('reportProgress')}</p>{/if}
        {#if create.error}<p class="error" role="alert">{t(create.error)}</p>{/if}
      </section>
      <aside class="panel">
        <h2>{t('reportHistory')}</h2>
        {#if !history.length}<p class="empty">{t('noReports')}</p>{/if}
        <div class="list" role="list">
          {#each history as row (row.id)}
            <div class="list-item" role="listitem"><span>#{row.id} · <strong>{t(row.kind === 'search' ? 'searchReport' : 'detectionReport')}</strong>
              <span class="muted report-meta">{row.species ? `${commonName(row.species, app.locale)} · ${row.species.scientific_name}` : t('reportAllSpecies')} · {row.start_date}–{row.end_date}</span></span>
              <Button size="sm" href={resolve(`/datasets/${datasetId}/reports/${row.id}`)} aria-label={t('open')}>{t('open')} ›</Button></div>
          {/each}
        </div>
      </aside>
    </div>
  {/if}
</div>

<style>
  .report-tabs, .actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
  .purpose, form, .specific-species { margin-top: 14px; }
  .option { margin: 14px 0; }
  .specific-species :global(.picked), .specific-species :global(label) { margin-top: 10px; }
  .report-meta { display: block; overflow-wrap: anywhere; }
</style>
