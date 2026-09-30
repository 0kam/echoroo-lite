<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName,speciesPage } from '$lib/species';
  import { openExternal } from '$lib/files';
  import { checkName,checkText,fill,listenLabel,presenceText,rangeQuery,type Check,type SpeciesResult,type Validation } from '$lib/workflow';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),modelId=$state(Number(initial.get('model_id'))||0),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0,about=$state<{ebird_code:string|null;in_quiz:boolean}|null>(null);
  // Which deletion is waiting for confirmation: a check's model key, or 'search'.
  let confirming=$state(''),preview=$state<{marks:number;labels:number;custom_models:number;checks:number}|null>(null);
  async function load(){const version=++request;loading=true;error='';
    const query=new URLSearchParams({engine});
    if(result){query.set('start_date',result.start_date);query.set('end_date',result.end_date);}
    else for(const key of ['start_date','end_date']){const value=initial.get(key);if(value)query.set(key,value);}
    if(engine==='custom'&&modelId)query.set('model_id',String(modelId));
    const url=`/datasets/${id}/species/${speciesId}?${query}`;
    try{const data=await api<SpeciesResult>(url);if(version===request){result=data;engine=data.report?.engine??engine;modelId=data.report?.model_id??0;liveUrl=url;
      // Keep the open tab in the address so the browser's back button returns to it.
      void goto(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${view}`,{replaceState:true,noScroll:true,keepFocus:true});}}
    catch(e){if(version===request)error=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===request)loading=false;}
  }
  onMount(()=>{void load();api<{ebird_code:string|null;in_quiz:boolean}>(`/species/${speciesId}/examples?dataset_id=${id}`).then(value=>about=value).catch(()=>{});});
  const external=$derived(result&&about?speciesPage(result.species,about.ebird_code,app.locale):null);
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):new URLSearchParams([...initial].filter(([key])=>key.endsWith('_date'))).toString());
  // The current view (period and chart tab), so pages opened from here can return to it.
  const view=$derived(`${range}&engine=${engine}${engine==='custom'&&modelId?`&model_id=${modelId}`:''}`);
  const presenceHref=(source:'ai'|'search')=>`${resolve(`/datasets/${id}/species/${speciesId}/presence`)}?${range}&source=${source}`;
  const modelHref=$derived(`${resolve(`/datasets/${id}/species/${speciesId}/model`)}?${range}`);
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${view}`)}`);
  const selected=$derived(result?.checks.find(item=>item.key===result?.selected)??null);
  // Every model finished its check and none reached the goal: a species model is the remaining route.
  const missed=$derived(!!result?.checks.length&&result.checks.every(item=>item.validation?.applicable&&['goal_not_met','undecided'].includes(item.validation.status)));
  const check=createTask(async(item:Check)=>{
    if(!result)return;
    const validation=item.validation?.applicable?item.validation:await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,
      start_date:result.start_date,end_date:result.end_date,model:item.engine,model_id:item.model_id});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const removeCheck=createTask(async(item:Check)=>{await api(`/validations/${item.validation!.id}/delete`,{});confirming='';await load();},{toast:true});
  const askSearchDelete=createTask(async()=>{preview=await api(`/search-runs/${result!.search!.id}/delete-preview`);confirming='search';},{toast:true});
  const removeSearch=createTask(async()=>{await api(`/search-runs/${result!.search!.id}/delete`,{});confirming='';preview=null;engine='auto';modelId=0;await load();},{toast:true});
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:result?.site.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
</script>
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}?${range}`}>{t('uBackResults')}</a><span>{result?.site.name}</span></div>
    <h1>{result?commonName(result.species,app.locale):t('species')} <i class="sci">{result?.species.scientific_name}</i></h1>
    {#if external}<Button size="sm" onclick={()=>void openExternal(external.url)}>{t('refLearn').replace('{site}',external.name)}</Button>{/if}</div>
  </div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    <div class="cards">
      <section class="panel card" aria-labelledby="presence-title">
        <h2 id="presence-title">{t('sPresenceTitle')}</h2>
        <p class="status"><strong>{presenceText(result.presence)}</strong></p>
        <p class="muted">{t('sPresenceHint')}</p>
        <div class="actions">
          {#if result.checks.length}<Button variant="primary" href={presenceHref('ai')}>{t('sListenCheck')}</Button>{/if}
          {#if result.search}<Button variant={result.checks.length?'default':'primary'} href={presenceHref('search')}>{t('sListenSearch')}</Button>{/if}
          {#if about?.in_quiz}<Button href={practice}>{t('wPractice')}</Button>{/if}
        </div>
        <p class="muted">{t('pEvidenceNote')}</p>
      </section>
      <section class="panel card" aria-labelledby="activity-title">
        <h2 id="activity-title">{t('sActivityTitle')}</h2>
        <p class="muted">{t('sActivityHint')}</p>
        {#each result.checks as item (item.key)}<div class="check-row">
          <div class="check-name"><strong>{checkName(item)}</strong><span>{checkText(item.validation)}</span></div>
          <div class="actions"><Button pending={check.pending} onclick={()=>check.run(item)}>{item.validation?listenLabel(item.validation):t('cStart')}</Button>
            {#if item.validation}<Button variant="ghost" onclick={()=>confirming=confirming===item.key?'':item.key}>{t('sDeleteCheck')}</Button>{/if}</div>
          {#if confirming===item.key}<div class="confirm" role="group" aria-label={t('sDeleteCheck')}><p>{fill('sDeleteCheckConfirm',{model:checkName(item)})}</p>
            <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeCheck.pending} onclick={()=>removeCheck.run(item)}>{t('sDeleteYes')}</Button></div></div>{/if}
        </div>{:else}<p>{t('wNoModel')}</p>{/each}
        {#if missed}<p>{t('sGoalHint')}</p>{/if}
        {#if result.search}<div class="actions"><Button href={modelHref}>{t('customModelTitle')}</Button></div>{:else}<p class="muted">{t('sModelNeedsSearch')}</p>{/if}
      </section>
    </div>
    {#if result.checks.length}<section class="panel">
      <h2>{t('sTimeTitle')}</h2>
      <div class="actions model-tabs">{#each result.checks as item (item.key)}<Button active={result.selected===item.key} aria-pressed={result.selected===item.key} disabled={loading}
        onclick={()=>{engine=item.engine;modelId=item.model_id??0;load();}}>{checkName(item)}</Button>{/each}</div>
      {#if selected}<QualityPanel validation={selected.validation}/>{/if}
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>
      {:else if result.report}{#key liveUrl}<ReportView report={result.report} {liveUrl}/>{/key}
      {:else}<p class="empty">{t('wNoModel')}</p>{/if}
    </section>{/if}
    <section class="panel">
      <h2>{t('sSearchTitle')}</h2><p class="muted">{t('sSearchHint')}</p>
      {#if result.search}<p>{fill('sSearchDone',{date:when(result.search.created_at)})}</p>
        <div class="actions"><Button href={presenceHref('search')}>{t('sListenSearch')}</Button>
          <Button variant="ghost" pending={askSearchDelete.pending} onclick={()=>confirming==='search'?confirming='':askSearchDelete.run()}>{t('sSearchDelete')}</Button></div>
        {#if confirming==='search'&&preview}<div class="confirm" role="group" aria-label={t('sSearchDelete')}><p>{t('sSearchDeleteConfirm')}</p>
          {#if preview.marks||preview.labels||preview.custom_models}<ul>
            {#if preview.marks}<li>{fill('sSearchDeleteMarks',{n:preview.marks})}</li>{/if}
            {#if preview.labels}<li>{fill('sSearchDeleteLabels',{n:preview.labels})}</li>{/if}
            {#if preview.custom_models}<li>{fill('sSearchDeleteModels',{n:preview.custom_models})}</li>{/if}</ul>
          {:else}<p>{t('sSearchDeleteNothing')}</p>{/if}
          <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeSearch.pending} onclick={()=>removeSearch.run()}>{t('sSearchDelete')}</Button></div></div>{/if}
      {:else}<Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${view}`}>{t('sSearchStart')}</Button>{/if}
    </section>
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:16px;align-items:start}.card{display:flex;flex-direction:column;gap:12px}
  .status{font-size:18px}.check-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:10px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .check-name{display:flex;flex-direction:column}.confirm{flex-basis:100%;padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}.confirm p,.confirm ul{margin:0 0 10px}
  .model-tabs{margin:12px 0 4px}section p{margin:8px 0}</style>
