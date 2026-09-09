<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { percent } from '$lib/reports';
  import type { Report } from '$lib/reports';
  import type { Validation } from '$lib/workflow';
  import ReportView from '$lib/ReportView.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  const datasetId = $derived(Number(page.params.id)), reportId = $derived(Number(page.params.report));
  let report = $state<(Report & {validation?:Validation|null}) | null>(null), error = $state('');
  $effect(() => {
    const id=reportId;error='';report=null;
    api<Report>(`/reports/${id}`).then(value=>{if(id===reportId)report=value;}).catch(e=>{if(id===reportId)error=e.message;});
  });
</script>
<div class="page">
  <div class="crumbs"><a href={resolve(`/datasets/${datasetId}/reports`)}>{t('wExportHistory')}</a></div>
  <h1>{t('reportResult')} #{reportId}</h1><p>{t('reportSnapshotHint')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>
  {:else if report}
    <div class="two-col"><section class="panel">{#if !report.species}<table><thead><tr><th>{t('species')}</th><th>{t('candidateRate')}</th></tr></thead><tbody>{#each report.summaries as row}<tr><td>{commonName(row.species,app.locale)} <i>{row.species.scientific_name}</i></td><td>{percent(row.candidate_rate)}</td></tr>{/each}</tbody></table>{/if}{#key report.id}<ReportView {report}/>{/key}</section>
      <aside class="panel"><QualityPanel validation={report.validation??null}/></aside></div>
  {:else}<p><Spinner/> {t('loading')}</p>{/if}
</div>
