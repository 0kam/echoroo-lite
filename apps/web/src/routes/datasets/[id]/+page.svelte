<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,refreshCatalog } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { qualityStatus,rangeQuery,type Overview } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=$derived(Number(page.params.id));
  const job=$derived(activeJob());
  const jobSignature=$derived(app.jobs.filter(j=>j.dataset_id===id).map(j=>`${j.id}:${j.status}`).join(','));
  let result=$state<Overview|null>(null),first=$state(''),last=$state(''),loading=$state(true),error=$state('');
  let request=0;
  async function load(from='',to='') {
    const version=++request;loading=true;error='';
    try {const data=await api<Overview>(`/datasets/${id}/overview${from&&to?`?${rangeQuery(from,to)}`:''}`);
      if(version===request){result=data;first=data.start_date;last=data.end_date;}}
    catch(e){if(version===request)error=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===request)loading=false;}
  }
  $effect(()=>{const current=id, signature=jobSignature, hash=page.url.hash;void current;void signature;const query=new URL(hash.slice(1)||page.url.pathname+page.url.search,page.url).searchParams;untrack(()=>void load(query.get('start_date')??'',query.get('end_date')??''));});
  const analyze=createTask(async()=>{await api('/jobs',{dataset_id:id,recording_id:null});await refreshCatalog();},{toast:true});
  const range=$derived(result?`?${rangeQuery(result.start_date,result.end_date)}`:'');
</script>
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span>{result?.site.name}</div>
    <h1>{result?.dataset.name??t('wResults')}</h1><p>{result?.site.timezone}</p></div>
    <Button href={`${resolve(`/datasets/${id}/reports`)}${range}`} variant="primary" disabled={!result?.species.length}>{t('wExport')}</Button></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    <section class="panel"><div class="section-heading"><div class="stat-row"><span><strong>{result.analysed_recordings} / {result.records}</strong> {t('files')}</span>
      <span><strong>{(result.recorded_seconds/3600).toFixed(1)} h</strong></span>
      <span>{t(result.analysed_recordings===result.records?'wComplete':'wPartial')}</span></div>
      <div class="actions"><Button href={resolve(`/datasets/${id}/recordings`)}>{t('wViewRecordings')}</Button>
        {#if result.mixed_models||result.analysed_recordings<result.records}<Button pending={analyze.pending} disabled={!!job} onclick={()=>analyze.run()}>{t('analyzeAll')}</Button>{/if}</div></div>
      {#if result.mixed_models}<p class="notice">{t('wModelVersions')}</p>{/if}
      {#if job?.dataset_id===id}<JobStatus {job}/>{/if}
      <form class="actions" onsubmit={e=>{e.preventDefault();goto(`${resolve(`/datasets/${id}`)}?${rangeQuery(first,last)}`);}}>
        <label>{t('reportFrom')}<input type="date" bind:value={first} max={last} required/></label>
        <label>{t('reportTo')}<input type="date" bind:value={last} min={first} required/></label>
        <Button type="submit" pending={loading}>{t('wApply')}</Button>
      </form>
    </section>
    <section class="panel"><div class="section-heading"><h2>{t('wSpecies')}</h2><Button href={`${resolve(`/datasets/${id}/search`)}${range}`}>{t('wMissing')}</Button></div>
      <p class="muted">{t('wOverviewHint')}</p>
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
      <div class="table-wrap"><table><thead><tr><th>{t('species')}</th><th>{t('wDetection')}</th><th>{t('wConfirmed')}</th><th>{t('wQuality')}</th></tr></thead><tbody>
        {#each result.species as row (row.species.id)}<tr><td><a href={`${resolve(`/datasets/${id}/species/${row.species.id}`)}${range}`}><strong>{commonName(row.species,app.locale)}</strong><i class="scientific-name">{row.species.scientific_name}</i></a></td>
          <td>{#each row.models as model}<div>{model.engine==='birdnet'?'BirdNET':'Perch'}: {model.candidate_days} {t('wDays')}</div>{:else}—{/each}</td>
          <td>{row.confirmed||t('wNoConfirmed')}</td><td>{t(qualityStatus(row.validation))}</td></tr>
        {:else}<tr><td colspan="4">{t(result.models.length?'wNoSpecies':'wNoAnalysis')}</td></tr>{/each}
      </tbody></table></div>
    </section>
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>form{margin-top:16px;align-items:flex-end}td a{text-decoration:none;color:inherit}.table-wrap{margin-top:16px}td{vertical-align:middle}</style>
