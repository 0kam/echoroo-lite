<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,datasetOf,dayRange,duration,refreshCatalog,siteOf } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { activity,activityText,analysisSeconds,checkName,fill,presenceText,rangeQuery,remainingSounds,roughTime,speciesGroup,workload,type Overview } from '$lib/workflow';
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
  const order=['confirmed','missed','check','few','rare'] as const;
  const groups=$derived(Object.fromEntries(order.map(key=>[key,result?.species.filter(row=>speciesGroup(row,result!.recorded_seconds)===key)??[]])) as Record<typeof order[number],Row[]>);
  // The next step: the most-detected species nobody has listened to yet; then the few detections (A5); species rare here only when nothing else is left.
  const stage=$derived(groups.check.length?'check':groups.few.length?'few':'rare');
  const next=$derived(groups[stage].find(row=>row.presence.status==='unchecked'));
  const speciesHref=(row:Row)=>`${resolve(`/datasets/${id}/species/${row.species.id}`)}${range}&engine=${row.models.find(m=>(m.candidate_windows??0)>0)?.engine??'auto'}`;
  const reports=$derived(`${resolve(`/datasets/${id}/reports`)}${range}`);
  const titles={confirmed:'wGroupConfirmed',missed:'wGroupMissed',check:'wGroupCheck',few:'wGroupFew',rare:'wGroupRare'};
  // A column that says the same thing on every row becomes one note above the table.
  const anyActivity=$derived(!!result?.species.some(row=>activity(row.models)!=='not_started'));
  // Listening still needed for "when and how much" on the species worth checking (A2, B3: one AI each).
  const left=$derived([...groups.confirmed,...groups.check].map(row=>remainingSounds(row.models)).filter(Boolean));
  const effort=$derived(workload(left.reduce((sum,sounds)=>sum+sounds,0)));
  const days=$derived(result?Math.round((Date.parse(result.end_date)-Date.parse(result.start_date))/86400000)+1:0);
  const unanalysed=$derived(result?result.records-result.analysed_recordings:0);
  const estimate=$derived(result?analysisSeconds(result.pending_analysis):null);
  // Over 30 minutes, point to the faster Kinglet AIs unless one is already the main AI.
  const suggestFaster=$derived(estimate!=null&&estimate>1800&&!app.ai.main.startsWith('kinglet')&&app.ai.models.some(m=>m.id.startsWith('kinglet')&&m.available));
  const jobHere=$derived(job&&app.datasets.find(d=>d.id===job.dataset_id)?.site_id===result?.site.id?job:null);
</script>
{#snippet table(rows:Row[])}<div class="table-wrap"><table><colgroup><col class="name"/><col/><col class="state"/>{#if anyActivity}<col class="state"/>{/if}</colgroup>
  <thead><tr><th>{t('species')}</th><th>{t('wDetection')}</th><th>{t('wPresence')}</th>{#if anyActivity}<th>{t('wActivity')}</th>{/if}</tr></thead><tbody>
  {#each rows as row (row.species.id)}<tr><td><a href={speciesHref(row)}><strong>{commonName(row.species,app.locale)}</strong>{#if row.region==='unusual'} <span class="rare">{t('wUnusual')}</span>{/if}<i class="scientific-name">{row.species.scientific_name}</i></a></td>
    <td>{#each row.models as model}<div>{checkName(model)}: {model.candidate_windows==null?'—':fill('wDetectionCount',{n:model.candidate_windows.toLocaleString(app.locale),days:model.candidate_days??0})}</div>{:else}—{/each}</td>
    <td>{presenceText(row.presence)}</td>
    {#if anyActivity}<td>{activityText(row.models)}</td>{/if}</tr>{/each}
</tbody></table></div>{/snippet}
<div class="page">
  <div class="crumbs"><a href={resolve('/datasets')}>{t('uBackSites')}</a></div>
  <div class="page-header"><div>
    <h1>{result?.site.name??siteOf(datasetOf(id))?.name??t('wResults')}</h1>{#if result}<p>{dayRange(result.start_date,result.end_date)}{fill('wDaysSpan',{n:days})}</p>{/if}</div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if (Date.parse(result.last_date)-Date.parse(result.first_date))/86400000>=366}<label class="year">{t('uYear')}<select value={first.slice(0,4)} onchange={e=>goto(`${resolve(`/datasets/${id}`)}?${rangeQuery(e.currentTarget.value+'-01-01',e.currentTarget.value+'-12-31')}`)}>{#each result.years as year}<option value={String(year)}>{year}</option>{/each}</select></label>{/if}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    {#if result.species.length&&result.analysed_recordings===result.records&&!result.location_stale}<section class="panel next-step">
      <p><strong>{t(!next?'wNextExport':groups.confirmed.length?'wNextMore':'wNextListen')}</strong></p>
      <p>{fill('wProgress',{total:result.species.length-(stage==='check'?groups.few.length:0)-(stage==='rare'?0:groups.rare.length),confirmed:groups.confirmed.length,missed:groups.missed.length})}</p>
      {#if left.length}<p class="muted">{fill('wWorkload',{n:left.length,time:effort.time})}</p>{/if}
      <div class="actions">{#if next}<Button variant="primary" href={speciesHref(next)}>{fill('wNextOpen',{name:commonName(next.species,app.locale)||next.species.scientific_name})}</Button>{/if}
        <Button variant={next?'default':'primary'} href={reports}>{t('wExport')}</Button></div></section>{/if}
    <section class="panel stack"><div class="section-heading"><div class="stat-row"><span>{fill('wRecordingsAnalysed',{analysed:result.analysed_recordings,records:result.records})}</span>
      <span>{fill('wTotalLength',{duration:duration(result.recorded_seconds)})}</span>
      <span>{t(result.analysed_recordings===result.records?'wComplete':result.analysed_recordings?'wPartial':'cell_unanalysed')}</span></div></div>
      {#if result.mixed_models}<p class="notice">{t('wModelVersions')}</p>{/if}
      <!-- Before and during analysis the box is already there, so progress does not push the page around. -->
      {#if jobHere||unanalysed>0}<div class="status-box" role="status">
        {#if jobHere}<JobStatus job={jobHere}/>{:else}<p><strong>{fill('wAnalyzeNeeded',{n:unanalysed})}</strong></p><p class="muted">{t('analyzeAllHint')}</p>{/if}</div>{/if}
      <div class="actions">
        {#if result.mixed_models||unanalysed>0}<Button variant={unanalysed>0?'primary':'default'} pending={analyze.pending} disabled={!!job} onclick={()=>analyze.run()}>{t('analyzeAll')}</Button>{/if}
        <Button href={`${resolve('/datasets/new')}?site=${result.site.id}&return=${encodeURIComponent(`/datasets/${id}${range}`)}`}>{t('uAddRecordings')}</Button><Button href={resolve(`/datasets/${id}/recordings`)}>{t('wViewRecordings')}</Button></div>
      {#if (result.mixed_models||unanalysed>0)&&!job&&estimate!=null}<p class="muted">{fill('analysisEstimate',{time:roughTime(estimate)})}
        {#if suggestFaster}{t('analysisFaster')} · <a href={`${resolve('/settings')}?return=${encodeURIComponent(`/datasets/${id}${range}`)}`}>{t('navSettings')}</a>{/if}</p>{/if}
    </section>
    {#if result.models.length||result.species.length}<section class="panel"><div class="section-heading"><h2>{t('wSpecies')}</h2><Button href={`${resolve(`/datasets/${id}/search`)}${range}`}>{t('sFindMissing')}</Button></div>
      <p class="muted">{t('wOverviewHint')}</p><p class="muted">{t('wDetectionCountHint')}</p>{#if result.species.length&&!anyActivity}<p class="muted">{t('wActivityNoneAll')}</p>{/if}
      {#if loading}<p role="status"><Spinner/> {t('wPreparing')}</p>{/if}
      {#each order as key}{@const rows=groups[key]}{#if rows.length}
        {#if key==='few'||key==='rare'}<details class="group" open={stage===key}><summary><h3>{t(titles[key])} · {fill('wSpeciesCount',{n:rows.length})}</h3></summary><p class="notice">{t(key==='few'?'wFewCaution':'wRareHint')}</p>{@render table(rows)}</details>
        {:else}<h3 class="group">{t(titles[key])} · {fill('wSpeciesCount',{n:rows.length})}</h3>{#if key==='missed'}<p class="muted">{t('wMissedHint')}</p>{/if}{@render table(rows)}{/if}
      {/if}{/each}
      {#if !result.species.length}<p>{t(result.models.length?'wNoSpecies':'wNoAnalysis')}</p>{/if}
    </section>{:else}<p>{t('wNoAnalysis')}</p>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('wPreparing')}</p>{/if}
</div>
<style>.year{max-width:180px}.page-header p{font-size:16px}td a{display:block;min-height:44px;text-decoration:none;color:inherit}td a:hover strong{text-decoration:underline}.table-wrap{margin-top:8px}td{vertical-align:middle}.group{margin-top:24px}h3.group,.group h3{font-size:18px;margin-bottom:0}details.group>summary{cursor:pointer}details.group>summary h3{display:inline}.next-step p{margin:0 0 12px}table{table-layout:fixed}col.name{width:28%}col.state{width:22%}td{overflow-wrap:break-word}</style>
