<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { t, datasetOf } from '$lib/app.svelte';
  import type { Species } from '$lib/species';
  import type { Report } from '$lib/reports';
  import ReportView from '$lib/ReportView.svelte';
  import SimilarityDistribution from '$lib/SimilarityDistribution.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  const datasetId = $derived(Number(page.params.id)), reportId = $derived(Number(page.params.report));
  const dataset = $derived(datasetOf(datasetId));
  let report = $state<Report | null>(null), loadError = $state('');
  async function show(created: Report) {
    report = created; notify('reportCreated');
    await goto(resolve(`/datasets/${datasetId}/reports/${created.id}`));
  }
  const chooseSpecies = createTask(async (species: Species) => {
    if (!report || report.kind !== 'detection') return;
    await show(await api<Report>('/reports/detection', { dataset_id: datasetId, engine: report.engine,
      model_sha256: report.model_sha256, threshold: report.threshold, start_date: report.start_date,
      end_date: report.end_date, species_id: species.id, base_report_id: report.id }));
  }, { toast: true });
  const useSuggestion = createTask(async () => {
    if (!report?.threshold_suggestion) return;
    await show(await api<Report>('/reports/search', { run_id: report.run_id, start_date: report.start_date,
      end_date: report.end_date, threshold: report.threshold_suggestion.threshold }));
  }, { toast: true });
  $effect(() => {
    const id = reportId;
    loadError = ''; report = null;
    void api<Report>(`/reports/${id}`).then(value => { if (id === reportId) report = value; })
      .catch(e => { if (id === reportId) loadError = e instanceof Error ? e.message : 'unknown_error'; });
  });
</script>

<div class="page">
  <div class="page-header">
    <div><div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span><a href={resolve(`/datasets/${datasetId}`)}>{dataset?.name}</a><span>›</span><a href={resolve(`/datasets/${datasetId}/reports`)}>{t('reports')}</a><span>›</span></div>
      <h1>{t('reportResult')} #{reportId}</h1><p>{t('reportSnapshotHint')}</p></div>
    <Button href={resolve(`/datasets/${datasetId}/reports`)}>‹ {t('backToReports')}</Button>
  </div>
  {#if loadError}<div class="panel empty"><p class="error">{t(loadError === 'not_found' ? 'reportNotFound' : loadError)}</p></div>
  {:else if !report}<p class="muted"><Spinner /> {t('loading')}</p>
  {:else}
    {#if report.kind === 'search' && report.distribution}
      <section class="panel">
        <h2>{t('similarityDistribution')}</h2>
        <SimilarityDistribution bands={report.distribution} hint={t('distributionHint')} />
        {#if report.threshold_suggestion}
          <div class="suggestion"><p class="muted">{t('suggestedDisplayThreshold')}: {report.threshold_suggestion.threshold.toFixed(3)} · {t('thresholdSuggestionHint')}</p>
            <Button size="sm" pending={useSuggestion.pending} onclick={() => useSuggestion.run()}>{t('useSuggestedThreshold')}</Button></div>
        {:else}<p class="muted suggestion">{t('noThresholdSuggestion')}</p>{/if}
        {#if useSuggestion.error}<p class="error" role="alert">{t(useSuggestion.error)}</p>{/if}
      </section>
    {/if}
    <section class="panel">
      {#key report.id}<ReportView {report} onspecies={species => chooseSpecies.run(species)} />{/key}
      {#if chooseSpecies.pending}<p class="muted" role="status">{t('reportProgress')}</p>{/if}
      {#if chooseSpecies.error}<p class="error" role="alert">{t(chooseSpecies.error)}</p>{/if}
    </section>
  {/if}
</div>

<style>
  .suggestion { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 10px; margin-top: 12px; }
</style>
