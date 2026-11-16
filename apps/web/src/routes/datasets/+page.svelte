<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,refreshCatalog } from '$lib/app.svelte';
  import { fill } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type State={site_id:number;records:number;analysed:number};
  // Counted again whenever a job starts or ends, so a finished analysis changes the main button.
  let states=$state<State[]>([]);
  const jobSignature=$derived(app.jobs.map(j=>`${j.id}:${j.status}`).join(','));
  $effect(()=>{void jobSignature;void app.datasets.length;api<State[]>('/sites/states').then(list=>states=list,()=>{});});
  const job=$derived(activeJob());
  const analyze=createTask(async(dataset:number)=>{await api('/jobs',{dataset_id:dataset,recording_id:null});await refreshCatalog();await goto(resolve(`/datasets/${dataset}`));},{toast:true});
</script>
<div class="page">
  <div class="page-header"><div><h1>{t('uSites')}</h1><p>{t('uSitesHint')}</p></div>
    {#if app.sites.length}<Button href={resolve('/datasets/new')}>{t('uNewSite')}</Button>{/if}</div>
  {#if !app.sites.length}<section class="panel empty"><p>{t('uFirstSite')}</p><Button variant="primary" href={resolve('/datasets/new')}>{t('uNewSite')}</Button></section>{/if}
  {#each app.sites as site}
    {@const datasets=app.datasets.filter(d=>d.site_id===site.id).sort((a,b)=>a.id-b.id)}
    {@const state=states.find(s=>s.site_id===site.id)}
    {@const running=!!job&&datasets.some(d=>d.id===job.dataset_id)}
    {@const needsAnalysis=!!state&&state.analysed<state.records&&!running}
    <section class="panel section-heading"><div><h2>{site.name}</h2>
      {#if state}<p class="muted">{fill('wRecordingsAnalysed',{analysed:state.analysed,records:state.records})}{#if running} · {t('analysisInProgress')}{/if}</p>{/if}</div>
      <div class="actions">
      <Button href={`${resolve('/datasets/new')}?site=${site.id}`}>{t('uAddRecordings')}</Button>
      {#if datasets.length}
        {#if needsAnalysis}<Button variant="primary" pending={analyze.pending} disabled={!!job} onclick={()=>analyze.run(datasets[0].id)}>{t('analyzeAll')}</Button>{/if}
        <Button variant={needsAnalysis?'default':'primary'} href={resolve(`/datasets/${datasets[0].id}`)}>{t('uSeeResults')}</Button>{/if}
      <Button variant="ghost" href={resolve(`/sites/${site.id}/manage`)}>{t('mManage')}</Button>
    </div></section>
  {/each}
</div>
<style>.section-heading p{margin:4px 0 0}</style>
