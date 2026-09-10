<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,duration,refreshCatalog } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { qualityStatus,rangeQuery,type Overview } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=$derived(Number(page.params.id));
  const job=$derived(activeJob());
  const jobSignature=$derived(app.jobs.filter(j=>app.datasets.find(d=>d.id===j.dataset_id)?.site_id===app.datasets.find(d=>d.id===id)?.site_id).map(j=>`${j.id}:${j.status}`).join(','));
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
    <h1>{result?.site.name??t('wResults')}</h1><p>{result?.start_date} — {result?.end_date}</p></div>
    {#if result?.species.length}<Button href={`${resolve(`/datasets/${id}/reports`)}${range}`} variant="primary">{t('wExport')}</Button>{/if}</div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if (Date.parse(result.last_date)-Date.parse(result.first_date))/86400000>=366}<label class="year">{t('uYear')}<select value={first.slice(0,4)} onchange={e=>goto(`${resolve(`/datasets/${id}`)}?${rangeQuery(e.currentTarget.value+'-01-01',e.currentTarget.value+'-12-31')}`)}>{#each result.years as year}<option value={String(year)}>{year}</option>{/each}</select></label>{/if}
    <section class="panel"><div class="section-heading"><div class="stat-row"><span><strong>{result.analysed_recordings} / {result.records}</strong> {t('files')}</span>
      <span><strong>{result.recorded_seconds<3600?duration(result.recorded_seconds):(result.recorded_seconds/3600).toFixed(1)+' h'}</strong></span>
      <span>{t(result.analysed_recordings===result.records?'wComplete':result.analysed_recordings?'wPartial':'cell_unanalysed')}</span></div>
      <div class="actions"><Button href={`${resolve('/datasets/new')}?site=${result.site.id}`}>{t('uAddRecordings')}</Button><Button href={resolve(`/datasets/${id}/recordings`)}>{t('wViewRecordings')}</Button>
        {#if result.mixed_models||result.analysed_recordings<result.records}<Button pending={analyze.pending} disabled={!!job} onclick={()=>analyze.run()}>{t('analyzeAll')}</Button>{/if}</div></div>
      {#if result.mixed_models}<p class="notice">{t('wModelVersions')}</p>{/if}
      {#if job&&app.datasets.find(d=>d.id===job.dataset_id)?.site_id===result.site.id}<JobStatus {job}/>{/if}

    </section>
    {#if result.models.length||result.species.length}<section class="panel"><div class="section-heading"><h2>{t('wSpecies')}</h2><Button href={`${resolve(`/datasets/${id}/search`)}${range}`}>{t('wMissing')}</Button></div>
      <p class="muted">{t('wOverviewHint')}</p><p class="muted">{t('wDetectionCountHint')}</p>
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
      <div class="table-wrap"><table><thead><tr><th>{t('species')}</th><th>{t('wDetection')}</th><th>{t('wConfirmed')}</th><th>{t('wQuality')}</th></tr></thead><tbody>
        {#each result.species as row (row.species.id)}<tr><td><a href={`${resolve(`/datasets/${id}/species/${row.species.id}`)}${range}&engine=${row.models.find(m=>m.candidate_windows>0)?.engine??'auto'}`}><strong>{commonName(row.species,app.locale)}</strong><i class="scientific-name">{row.species.scientific_name}</i></a></td>
          <td>{#each row.models as model}<div>{model.engine==='birdnet'?'BirdNET':'Perch'}: {model.candidate_windows.toLocaleString(app.locale)} {t('wDetectedWindows')} · {model.candidate_days} {t('wDays')}</div>{:else}—{/each}</td>
          <td>{row.confirmed||t('wNoConfirmed')}</td><td>{t(qualityStatus(row.validation))}</td></tr>
        {:else}<tr><td colspan="4">{t(result.models.length?'wNoSpecies':'wNoAnalysis')}</td></tr>{/each}
      </tbody></table></div>
    </section>{:else}<p>{t('wNoAnalysis')}</p>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.year{max-width:180px}td a{display:block;min-height:44px;text-decoration:none;color:inherit}td a:hover strong{text-decoration:underline}.table-wrap{margin-top:16px}td{vertical-align:middle}</style>
