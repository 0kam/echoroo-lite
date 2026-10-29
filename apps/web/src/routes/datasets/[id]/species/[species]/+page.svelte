<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { activity,activityText,checkName,checkText,defaultModel,fill,listenLabel,presenceText,rangeQuery,remainingSounds,windowSeconds,workload,type Check,type Overview,type SpeciesResult,type Validation } from '$lib/workflow';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),modelId=$state(Number(initial.get('model_id'))||0),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0,about=$state<{in_quiz:boolean}|null>(null),detected=$state<Overview['species'][number]['models']>([]);
  // Which deletion is waiting for confirmation: a check's model key, or 'search'.
  let confirming=$state(''),preview=$state<{marks:number;labels:number;custom_models:number;checks:number}|null>(null);
  // Each AI's payload for the current period, so switching chart tabs back and forth is instant.
  const cache=new Map<string,Promise<SpeciesResult>>();
  function urlFor(engine:string,modelId:number){const query=new URLSearchParams({engine});
    if(result){query.set('start_date',result.start_date);query.set('end_date',result.end_date);}
    else for(const key of ['start_date','end_date']){const value=initial.get(key);if(value)query.set(key,value);}
    if(engine==='custom'&&modelId)query.set('model_id',String(modelId));
    return `/datasets/${id}/species/${speciesId}?${query}`;
  }
  function fetchResult(url:string){let value=cache.get(url);
    if(!value){value=api<SpeciesResult>(url);cache.set(url,value);value.catch(()=>{if(cache.get(url)===value)cache.delete(url);});}
    return value;
  }
  // The current chart stays on screen while the next one loads, so the page keeps its height and scroll position.
  async function load(){const version=++request;loading=true;error='';
    try{const data=await fetchResult(urlFor(engine,modelId));if(version===request){result=data;engine=data.report?.engine??engine;modelId=data.report?.model_id??0;
      liveUrl=urlFor(engine,modelId);cache.set(liveUrl,Promise.resolve(data));
      // Keep the open tab in the address so the browser's back button returns to it.
      void goto(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${view}`,{replaceState:true,noScroll:true,keepFocus:true});
      // Prepare the other AIs' charts in the background.
      for(const item of data.checks)void fetchResult(urlFor(item.engine,item.model_id??0));}}
    catch(e){if(version===request)error=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===request)loading=false;}
  }
  onMount(()=>{void load();api<{in_quiz:boolean}>(`/species/${speciesId}/examples?dataset_id=${id}`).then(value=>about=value).catch(()=>{});
    // Detection counts per AI choose the AI to check (B3); the site overview has them and is usually cached.
    const dates=new URLSearchParams([...initial].filter(([key])=>key.endsWith('_date')));
    api<Overview>(`/datasets/${id}/overview?${dates}`).then(value=>detected=value.species.find(row=>row.species.id===speciesId)?.models??[]).catch(()=>{});});
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):new URLSearchParams([...initial].filter(([key])=>key.endsWith('_date'))).toString());
  // The current view (period and chart tab), so pages opened from here can return to it.
  const view=$derived(`${range}&engine=${engine}${engine==='custom'&&modelId?`&model_id=${modelId}`:''}`);
  const presenceHref=(source:'ai'|'search')=>`${resolve(`/datasets/${id}/species/${speciesId}/presence`)}?${range}&source=${source}`;
  const modelHref=$derived(`${resolve(`/datasets/${id}/species/${speciesId}/model`)}?${range}`);
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${view}`)}`);
  const selected=$derived(result?.checks.find(item=>item.key===result?.selected)??null);
  // B3: one AI is enough. The one already started, else the one with more detections; the others are optional.
  // Overview rows carry the detection and interval counts; the species payload carries the current checks.
  const counted=$derived(result?result.checks.map(item=>({...detected.find(model=>model.key===item.key),...item})):[]);
  const required=$derived(result?result.checks.find(item=>item.validation?.applicable)??result.checks.find(item=>item.key===defaultModel(counted)?.key)??result.checks[0]:undefined);
  const ordered=$derived(result&&required?[required,...result.checks.filter(item=>item!==required)]:[]);
  const settled=$derived(result?activity(result.checks):'not_started');
  const left=$derived(remainingSounds(counted)),remaining=$derived(left[0]),effort=$derived(workload(...left));
  // Every checked AI finished without a counting rule: a species-specific AI is the remaining route (B5).
  const missed=$derived(settled==='undecided');
  const listed=$derived(!!result?.checks.some(item=>item.engine!=='custom'));
  const showSearch=$derived(!!result&&(!listed||!!result.search||result.presence.status==='not_found'||missed));
  const showModel=$derived(!!result&&(!listed||missed||result.checks.some(item=>item.engine==='custom')));
  const check=createTask(async(item:Check)=>{
    if(!result)return;
    const validation=item.validation?.applicable?item.validation:await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,
      start_date:result.start_date,end_date:result.end_date,model:item.engine,model_id:item.model_id});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const removeCheck=createTask(async(item:Check)=>{await api(`/validations/${item.validation!.id}/delete`,{});confirming='';cache.clear();await load();},{toast:true});
  const askSearchDelete=createTask(async()=>{preview=await api(`/search-runs/${result!.search!.id}/delete-preview`);confirming='search';},{toast:true});
  const removeSearch=createTask(async()=>{await api(`/search-runs/${result!.search!.id}/delete`,{});confirming='';preview=null;cache.clear();engine='auto';modelId=0;await load();},{toast:true});
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:result?.site.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
</script>
<div class="page">
  <div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}?${range}`}>{t('uBackResults')}</a></div>
  <div class="page-header"><div>
    <h1>{result?commonName(result.species,app.locale):t('species')}{#if result?.region==='unusual'} <span class="rare">{t('wUnusual')}</span>{/if} <i class="sci">{result?.species.scientific_name}</i></h1></div>
  </div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    <div class="cards">
      <section class="panel card" aria-labelledby="presence-title">
        <h2 id="presence-title">{t('sPresenceTitle')}</h2>
        <p class="status"><strong>{presenceText(result.presence)}</strong></p>
        {#if result.presence.status==='not_found'}<p class="info">{t('sMissedDone')}</p>{:else}<p class="muted">{t('sPresenceHint')}</p>{/if}
        <div class="actions">
          {#if listed}<Button variant={result.presence.status==='unchecked'?'primary':'default'} href={presenceHref('ai')}>{t('sListenCheck')}</Button>{/if}
          {#if result.search}<Button variant={!listed&&result.presence.status==='unchecked'?'primary':'default'} href={presenceHref('search')}>{t('sListenSearch')}</Button>{/if}
          {#if about?.in_quiz}<Button href={practice}>{t('wPractice')}</Button>{/if}
        </div>
        <p class="muted">{t('pEvidenceNote')}</p>
      </section>
      <section class="panel card" aria-labelledby="activity-title">
        <h2 id="activity-title">{t('sActivityTitle')}</h2>
        {#if result.checks.length}<p class="status"><strong>{activityText(result.checks)}</strong></p>{/if}
        <p class="muted">{t(result.checks.length>1?'sActivityHint':'sActivityHintOne')}</p>
        {#if remaining&&result.checks.length}<p class="muted">{fill('sWorkload',{n:remaining,more:left[1]?t('vMore18'):'',low:effort.low,high:effort.high})}</p>{/if}
        {#each ordered as item (item.key)}<div class="check-row">
          <div class="check-name"><strong>{checkName(item)} <span class="muted">· {fill('wEvery',{n:windowSeconds(item.engine)})}{item===required?'':` · ${t('sOptional')}`}</span></strong><span>{checkText(item.validation)}</span></div>
          <Button variant={item===required&&result.presence.status==='confirmed'&&remaining?'primary':'default'} pending={check.pending} onclick={()=>check.run(item)}>{item.validation?listenLabel(item.validation):t('cStart')}</Button>
        </div>{:else}<p>{t('wNoModel')}</p>{/each}
        {#if missed}<p>{t('sUndecidedHint')}</p>{/if}
        {#if showModel}{#if result.search}<div class="actions"><Button href={modelHref}>{t('customModelTitle')}</Button></div>{:else}<p class="muted">{t('sModelNeedsSearch')}</p>{/if}{/if}
        <!-- Deleting is kept away from the listening buttons. -->
        {#if result.checks.some(item=>item.validation)}<details class="more"><summary>{t('sMore')}</summary>
          {#each result.checks.filter(item=>item.validation) as item (item.key)}<div class="stack">
            <Button variant="danger" size="sm" onclick={()=>confirming=confirming===item.key?'':item.key}>{t('sDeleteCheck')}：{checkName(item)}</Button>
            {#if confirming===item.key}<div class="confirm" role="group" aria-label={t('sDeleteCheck')}><p>{fill('sDeleteCheckConfirm',{model:checkName(item)})}</p>
              <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeCheck.pending} onclick={()=>removeCheck.run(item)}>{t('sDeleteYes')}</Button></div></div>{/if}
          </div>{/each}</details>{/if}
      </section>
    </div>
    {#if result.checks.length}<section class="panel">
      <h2>{t('sTimeTitle')}</h2>
      <div class="actions model-tabs">{#each result.checks as item (item.key)}<Button active={result.selected===item.key} aria-pressed={result.selected===item.key}
        onclick={()=>{engine=item.engine;modelId=item.model_id??0;load();}}>{checkName(item)}</Button>{/each}
        {#if loading}<span role="status"><Spinner/> {t('loading')}</span>{/if}</div>
      {#if selected}<QualityPanel validation={selected.validation} status={false}/>{/if}
      {#if result.report}{#key liveUrl}<ReportView report={result.report} {liveUrl}/>{/key}
      {:else}<p class="empty">{t('wNoModel')}</p>{/if}
    </section>{/if}
    {#if showSearch}<section class="panel">
      <h2>{t('sSearchTitle')}</h2><p class="muted">{t('sSearchHint')}</p>
      {#if result.search}<p>{fill('sSearchDone',{date:when(result.search.created_at)})}</p>
        <div class="actions"><Button href={presenceHref('search')}>{t('sListenSearch')}</Button></div>
        <details class="more"><summary>{t('sMore')}</summary>
          <Button variant="danger" size="sm" pending={askSearchDelete.pending} onclick={()=>confirming==='search'?confirming='':askSearchDelete.run()}>{t('sSearchDelete')}</Button>
        {#if confirming==='search'&&preview}<div class="confirm" role="group" aria-label={t('sSearchDelete')}><p>{t('sSearchDeleteConfirm')}</p>
          {#if preview.marks||preview.labels||preview.custom_models}<ul>
            {#if preview.marks}<li>{fill('sSearchDeleteMarks',{n:preview.marks})}</li>{/if}
            {#if preview.labels}<li>{fill('sSearchDeleteLabels',{n:preview.labels})}</li>{/if}
            {#if preview.custom_models}<li>{fill('sSearchDeleteModels',{n:preview.custom_models})}</li>{/if}</ul>
          {:else}<p>{t('sSearchDeleteNothing')}</p>{/if}
          <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeSearch.pending} onclick={()=>removeSearch.run()}>{t('sSearchDelete')}</Button></div></div>{/if}
        </details>
      {:else}<Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${view}`}>{t('sSearchStart')}</Button>{/if}
    </section>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('sPreparing')}</p>{/if}
</div>
<style>.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,380px),1fr));gap:16px;align-items:start}.card{display:flex;flex-direction:column;gap:12px}
  .status{font-size:18px}.check-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .check-name{display:flex;flex-direction:column}.confirm{padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}.confirm p,.confirm ul{margin:0 0 12px}
  .more{margin-top:8px}.more summary{min-height:44px;padding:10px 0}.more .stack{margin-top:8px}
  .model-tabs{margin:12px 0 4px}section p{margin:8px 0}</style>
