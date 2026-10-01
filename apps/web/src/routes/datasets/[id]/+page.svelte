<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,duration,refreshCatalog } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { checkName,fill,presenceText,rangeQuery,decidedModels,speciesGroup,type Overview } from '$lib/workflow';
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
  type Row=Overview['species'][number];
  const groups=$derived((['confirmed','check','few'] as const).map(key=>({key,rows:result?.species.filter(row=>speciesGroup(row)===key)??[]})));
  const confirmed=$derived(groups[0].rows.length>0);
  // The next step: the most-detected species nobody has listened to yet.
  const next=$derived(groups[1].rows.find(row=>row.presence.status==='unchecked'));
  const speciesHref=(row:Row)=>`${resolve(`/datasets/${id}/species/${row.species.id}`)}${range}&engine=${row.models.find(m=>(m.candidate_windows??0)>0)?.engine??'auto'}`;
  const reports=$derived(`${resolve(`/datasets/${id}/reports`)}${range}`);
  const titles={confirmed:'wGroupConfirmed',check:'wGroupCheck',few:'wGroupFew'};
</script>
{#snippet table(rows:Row[])}<div class="table-wrap"><table><thead><tr><th>{t('species')}</th><th>{t('wDetection')}</th><th>{t('wPresence')}</th><th>{t('wActivity')}</th></tr></thead><tbody>
  {#each rows as row (row.species.id)}{@const decided=decidedModels(row.models)}<tr><td><a href={speciesHref(row)}><strong>{commonName(row.species,app.locale)}</strong><i class="scientific-name">{row.species.scientific_name}</i></a></td>
    <td>{#each row.models as model}<div>{checkName(model)}: {model.candidate_windows==null?'—':`${model.candidate_windows.toLocaleString(app.locale)} ${t('wDetectedWindows')} · ${model.candidate_days} ${t('wDays')}`}</div>{:else}—{/each}</td>
    <td>{presenceText(row.presence)}</td>
    <td>{decided.length?fill('wDecidedModels',{models:decided.join(app.locale==='ja'?'・':', ')}):t('wNoDecided')}</td></tr>{/each}
</tbody></table></div>{/snippet}
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span>{result?.site.name}</div>
    <h1>{result?.site.name??t('wResults')}</h1><p>{result?.start_date} — {result?.end_date}</p></div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if (Date.parse(result.last_date)-Date.parse(result.first_date))/86400000>=366}<label class="year">{t('uYear')}<select value={first.slice(0,4)} onchange={e=>goto(`${resolve(`/datasets/${id}`)}?${rangeQuery(e.currentTarget.value+'-01-01',e.currentTarget.value+'-12-31')}`)}>{#each result.years as year}<option value={String(year)}>{year}</option>{/each}</select></label>{/if}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    {#if result.species.length&&result.analysed_recordings===result.records&&!result.location_stale}<section class="panel next-step"><p><strong>{t(confirmed?'wNextExport':'wNextListen')}</strong></p>
      <div class="actions">{#if confirmed}<Button variant="primary" href={reports}>{t('wExport')}</Button>{/if}
        {#if next}<Button variant={confirmed?'default':'primary'} href={speciesHref(next)}>{fill('wNextOpen',{name:commonName(next.species,app.locale)||next.species.scientific_name})}</Button>{/if}
        {#if !confirmed}<Button href={reports}>{t('wExport')}</Button>{/if}</div></section>{/if}
    <section class="panel"><div class="section-heading"><div class="stat-row"><span><strong>{result.analysed_recordings} / {result.records}</strong> {t('files')}</span>
      <span><strong>{result.recorded_seconds<3600?duration(result.recorded_seconds):(result.recorded_seconds/3600).toFixed(1)+' h'}</strong></span>
      <span>{t(result.analysed_recordings===result.records?'wComplete':result.analysed_recordings?'wPartial':'cell_unanalysed')}</span></div>
      <div class="actions"><Button href={`${resolve('/datasets/new')}?site=${result.site.id}&return=${encodeURIComponent(`/datasets/${id}${range}`)}`}>{t('uAddRecordings')}</Button><Button href={resolve(`/datasets/${id}/recordings`)}>{t('wViewRecordings')}</Button>
        {#if result.mixed_models||result.analysed_recordings<result.records}<Button pending={analyze.pending} disabled={!!job} onclick={()=>analyze.run()}>{t('analyzeAll')}</Button>{/if}</div></div>
      {#if result.mixed_models}<p class="notice">{t('wModelVersions')}</p>{/if}
      {#if job&&app.datasets.find(d=>d.id===job.dataset_id)?.site_id===result.site.id}<JobStatus {job}/>{/if}

    </section>
    {#if result.models.length||result.species.length}<section class="panel"><div class="section-heading"><h2>{t('wSpecies')}</h2><Button href={`${resolve(`/datasets/${id}/search`)}${range}`}>{t('sFindMissing')}</Button></div>
      <p class="muted">{t('wOverviewHint')}</p><p class="muted">{t('wDetectionCountHint')}</p>
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
      {#each groups as group}{#if group.rows.length}
        {#if group.key==='few'}<details class="group"><summary><h3>{t(titles[group.key])} · {fill('wSpeciesCount',{n:group.rows.length})}</h3></summary><p class="notice">{t('wFewCaution')}</p>{@render table(group.rows)}</details>
        {:else}<h3 class="group">{t(titles[group.key])} · {fill('wSpeciesCount',{n:group.rows.length})}</h3>{@render table(group.rows)}{/if}
      {/if}{/each}
      {#if !result.species.length}<p>{t(result.models.length?'wNoSpecies':'wNoAnalysis')}</p>{/if}
    </section>{:else}<p>{t('wNoAnalysis')}</p>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.year{max-width:180px}td a{display:block;min-height:44px;text-decoration:none;color:inherit}td a:hover strong{text-decoration:underline}.table-wrap{margin-top:8px}td{vertical-align:middle}.group{margin-top:28px}h3.group,.group h3{font-size:18px;margin-bottom:0}details.group>summary{cursor:pointer}details.group>summary h3{display:inline}.next-step p{margin:0 0 12px}</style>
