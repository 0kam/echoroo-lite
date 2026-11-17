<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,siteOf } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { modelIntro,activity,speciesGroup,activityLine,checkName,checkText,defaultModel,fill,listenLabel,presenceText,rangeQuery,remainingSounds,windowSeconds,workload,type Check,type Overview,type SpeciesResult,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const site=$derived(siteOf(datasetOf(id)));
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),modelId=$state(Number(initial.get('model_id'))||0),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0,about=$state<{in_quiz:boolean}|null>(null),detected=$state<Overview['species'][number]['models']|null>(null),recordedSeconds=$state(0);
  // Which deletion is waiting for confirmation: a check's model key, or 'search'.
  let confirming=$state(''),preview=$state<{marks:number;custom_models:number;checks:number}|null>(null);
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
    api<Overview>(`/datasets/${id}/overview?${dates}`).then(value=>{recordedSeconds=value.recorded_seconds;detected=value.species.find(row=>row.species.id===speciesId)?.models??[];}).catch(()=>{});});
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):new URLSearchParams([...initial].filter(([key])=>key.endsWith('_date'))).toString());
  // The current view (period and chart tab), so pages opened from here can return to it.
  const view=$derived(`${range}&engine=${engine}${engine==='custom'&&modelId?`&model_id=${modelId}`:''}`);
  const back=$derived(`${resolve(`/datasets/${id}`)}?${range}`);
  const presenceHref=(source:'ai'|'search')=>`${resolve(`/datasets/${id}/species/${speciesId}/presence`)}?${range}&source=${source}`;
  const searchHref=$derived(`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${view}`);
  const modelHref=$derived(`${resolve(`/datasets/${id}/species/${speciesId}/model`)}?${range}`);
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${view}`)}`);
  const selected=$derived(result?.checks.find(item=>item.key===result?.selected)??null);
  // B3: one AI is enough. The one already started, else the one with more detections; the others are optional.
  // Overview rows carry the detection and interval counts; the species payload carries the current checks.
  const counted=$derived(result?result.checks.map(item=>({...detected?.find(model=>model.key===item.key),...item})):[]);
  // An AI that found none of this species has nothing to check. The overview lists an AI for a species only when it has detections or a check;
  // until the overview arrives (or if it fails) every AI keeps its button.
  const undetected=(item:Check)=>{if(!detected||item.validation)return false;const row=detected.find(model=>model.key===item.key);return !row||row.candidate_windows===0;};
  const required=$derived(result?result.checks.find(item=>item.validation?.applicable)??result.checks.find(item=>item.key===defaultModel(counted)?.key)??result.checks[0]:undefined);
  const ordered=$derived(result&&required?[required,...result.checks.filter(item=>item!==required)]:[]);
  const settled=$derived(result?activity(result.checks):'not_started');
  const remaining=$derived(remainingSounds(counted)),effort=$derived(workload(remaining));
  // How far a species AI has grown while it has no current check: just made, or made again after its check.
  const grownOf=(item:Check)=>item.engine==='custom'&&(!item.validation||item.validation.model_reason==='custom_model_rebuilt')?item.growth:undefined;
  const growing=$derived(result?.checks.find(item=>grownOf(item)==='growing'));
  const noAI=$derived(!result?.checks.some(item=>item.engine!=='custom'));
  // No AI can identify this species or none found it: ① starts from an example-call search, and ② grows a species AI.
  const searchFirst=$derived(!!result&&!result.checks.some(item=>!undetected(item)));
  // The one open step (0 = both questions answered), from the agreed state table.
  const step=$derived(!result?0:growing||settled==='in_progress'?2:settled==='decided'||result.presence.status==='not_found'?0:result.presence.status==='unchecked'?1:2);
  // The AI the open step ② is about: the growing species AI, the check being answered, an unchecked species AI after an undecided check, else the required one.
  const main=$derived(growing??(settled==='in_progress'?result?.checks.find(item=>item.validation?.applicable&&item.validation.status==='in_progress'&&item.validation.counts.answered>0)
    :settled==='undecided'?result?.checks.find(item=>item.engine==='custom'&&!item.validation?.applicable):required));
  const mode=$derived(step!==2?'':growing?'growing':settled==='in_progress'?'continue':settled==='undecided'?main?'check':'undecided':searchFirst?'grow':'check');
  // The site list's caution for species with few detections and nothing done yet, shown here too.
  const few=$derived(!!result&&!!detected&&speciesGroup({species:result.species,models:detected,search:result.search,presence:result.presence,region:result.region},recordedSeconds)==='few');
  // With several AIs, say why this one: it found this species the most (B3).
  const mostDetections=$derived(!!main&&counted.filter(item=>!undetected(item)).length>1&&main.key===defaultModel(counted)?.key);
  // A delete confirmation opens below the fold: bring it into view and move the keyboard there.
  const reveal=(node:HTMLElement)=>{node.scrollIntoView({block:'nearest',behavior:'smooth'});node.focus({preventScroll:true});};
  const others=$derived(ordered.filter(item=>item!==main&&!undetected(item)));
  const answer2=$derived(!result?'':growing?`○ ${t('sqGrowing')}`:step===0&&settled==='not_started'?`− ${t('sqSkipped')}`
    :activityLine(result.checks));
  const check=createTask(async(item:Check)=>{
    if(!result)return;
    const validation=item.validation?.applicable?item.validation:await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,
      start_date:result.start_date,end_date:result.end_date,model:item.engine,model_id:item.model_id});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const removeCheck=createTask(async(item:Check)=>{await api(`/validations/${item.validation!.id}/delete`,{});confirming='';cache.clear();await load();},{toast:true});
  const askSearchDelete=createTask(async()=>{preview=await api(`/search-runs/${result!.search!.id}/delete-preview`);confirming='search';},{toast:true});
  const removeMiss=createTask(async()=>{await api('/presence/misses/delete',{dataset_id:id,species_id:speciesId});confirming='';cache.clear();await load();},{toast:true});
  const removeSearch=createTask(async()=>{await api(`/search-runs/${result!.search!.id}/delete`,{});confirming='';preview=null;cache.clear();engine='auto';modelId=0;await load();},{toast:true});
  // Sounds a person confirmed, in recording order; times are the site's, not the PC's.
  const heard=$derived(result?[...result.presence.evidence].sort((a,b)=>Date.parse(a.local_time)-Date.parse(b.local_time)):[]);
  // Where each confirmed sound was answered, as one line.
  const origins=$derived(new Intl.ListFormat(app.locale).format((['mark','check','label'] as const)
    .map(origin=>[origin,heard.filter(item=>item.origin===origin).length] as const).filter(([,n])=>n).map(([origin,n])=>fill(`sqFrom_${origin}`,{n}))));
  let allHeard=$state(false);
  const heardTime=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:result?.site.timezone}).format(new Date(value));
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',hourCycle:'h23',timeZone:result?.site.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
</script>
{#snippet listen(data:SpeciesResult,variant:'primary'|'default')}
  {#if !searchFirst}<Button {variant} href={presenceHref('ai')}>{t('sListenCheck')}</Button>
  {:else if data.search}<Button {variant} href={presenceHref('search')}>{t('sListenSearch')}</Button>
  {:else}<Button {variant} href={searchHref}>{t('sqSearchStart')}</Button>{/if}
{/snippet}
{#snippet checkRow(item:Check)}<div class="check-row">
  <div class="check-name"><strong>{checkName(item)}</strong><span>{undetected(item)?t('uNoDetection'):checkText(item.validation,item.growth)}</span></div>
  {#if !undetected(item)}<Button pending={check.pending} onclick={()=>check.run(item)}>{item.validation&&!grownOf(item)?listenLabel(item.validation):t('cStart')}</Button>{/if}
</div>{/snippet}
{#snippet figure(data:SpeciesResult)}
  {#if data.checks.length>1}<div class="actions model-tabs" role="group" aria-label={t('sqWhichAI')}><span class="muted">{t('sqWhichAI')}</span>
    {#each data.checks as item (item.key)}<Button active={data.selected===item.key} aria-pressed={data.selected===item.key}
      onclick={()=>{engine=item.engine;modelId=item.model_id??0;load();}}>{checkName(item)}</Button>{/each}</div>{/if}
  {#if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
  {#if selected}<QualityPanel validation={selected.validation} growth={selected.growth} status={false}/>{/if}
  {#if data.report}{#key liveUrl}<ReportView report={data.report} {liveUrl} secondary/>{/key}
  {:else}<p class="empty">{t('wNoModel')}</p>{/if}
{/snippet}
<div class="page">
  <div class="crumbs"><a href={back}>{t('uBackResults')}</a></div>
  <div class="page-header"><div>
    {#if site}<div class="site-name">{site.name}</div>{/if}
    <!-- No placeholder name while loading: the heading appears with the species. -->
    {#if result}<h1>{commonName(result.species,app.locale)}{#if result.region==='unusual'} <span class="rare">{t('wUnusual')}</span>{/if} <i class="sci">{result.species.scientific_name}</i></h1>{/if}</div>
  </div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if few}<p class="notice">{t('wFewCaution')}</p>{/if}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    <!-- Two questions as numbered steps: only the open one has its explanation and the page's one main button. -->
    <div class="steps">
      <section class="panel step-card" class:open={step===1} aria-current={step===1?'step':undefined} aria-labelledby="q1">
        <div class="step-head"><h2 id="q1">{t('sqQ1')}</h2><p class="status"><strong>{presenceText(result.presence)}</strong></p></div>
        {#if step===1}
          <p>{searchFirst?`${t(noAI?'sqNoAI':'sqNoDetection')} ${t('sqSearchHow')}`:t('sqListenHint')} {t('sqOnce')}</p>
          {#if searchFirst&&result.search}<p class="muted">{fill('sSearchDone',{date:when(result.search.created_at)})}</p>{/if}
          <div class="actions">{@render listen(result,'primary')}
            {#if !searchFirst&&result.search}<Button href={presenceHref('search')}>{t('sListenSearch')}</Button>{/if}</div>
          {#if about?.in_quiz}<p class="muted">{t('sqPracticeHint')} <a href={practice}>{t('wPractice')}</a></p>{/if}
        {:else if result.presence.status==='unchecked'}<div class="actions">{@render listen(result,'default')}</div>
        {:else if result.presence.status==='not_found'}<p>{t('sqNotFoundNote')}</p>
          <details class="more"><summary>{t('sqSearchMore')}</summary><p>{t('sqSearchHow')}</p>
            {#if result.search}<p class="muted">{fill('sSearchDone',{date:when(result.search.created_at)})}</p><Button href={presenceHref('search')}>{t('sListenSearch')}</Button>
            {:else}<Button href={searchHref}>{t('sqSearchStart')}</Button>{/if}</details>{/if}
        {#if heard.length}<details class="more"><summary>{fill('sqHeardOpen',{n:heard.length})}</summary>
          <p class="muted">{fill('sqHeardFrom',{list:origins})}</p><p class="muted">{t('sHeardNote')}</p>
          {#each allHeard?heard:heard.slice(0,10) as item (`${item.recording_id}:${item.start_s}:${item.end_s}`)}<article class="heard">
            <span>{heardTime(item.local_time)}</span>
            <AudioRegion source={{id:item.recording_id,kind:'recording',path:item.source_path,seconds:item.seconds}} fixedRange compact start={item.start_s} end={item.end_s}/>
          </article>{/each}
          {#if !allHeard&&heard.length>10}<Button onclick={()=>allHeard=true}>{t('sHeardAll')}</Button>{/if}</details>{/if}
      </section>
      <section class="panel step-card" class:open={step===2} aria-current={step===2?'step':undefined} aria-labelledby="q2">
        <div class="step-head"><h2 id="q2">{t('sqQ2')}</h2><p class="status"><strong>{answer2}</strong></p></div>
        {#if mode==='growing'&&growing}<div class="check-row">
            <div class="check-name"><strong>{checkName(growing)}</strong><span>{t('mdGrow_growing')}</span></div>
            <div class="actions"><Button variant="primary" href={modelHref}>{t('sqKeepGrowing')}</Button>
              {#if !undetected(growing)}<Button pending={check.pending} onclick={()=>check.run(growing)}>{t('cStart')}</Button>{/if}</div></div>
          <p class="muted">{t('sqGrowingNote')}</p>
        {:else if mode==='check'&&main}
          {#if remaining}<p>{fill('sqCheckHint',{n:remaining,time:effort.time})}</p>{/if}
          <p class="muted">{t('sqEvidence')} {fill('sqCheckAI',{model:main.engine==='custom'?checkName(main):modelIntro(main.engine)})}{#if mostDetections} {t('sqMostDetections')}{/if} {fill('wEveryNote',{n:windowSeconds(main.engine)})}</p>
          <Button variant="primary" pending={check.pending} onclick={()=>check.run(main)}>{t('sqCheckAnswers')}</Button>
        {:else if mode==='continue'&&main?.validation}
          <p>{fill('sqContinueHint',{n:remaining,time:effort.time})}</p><p class="muted">{t('sqEvidence')}</p>
          <Button variant="primary" pending={check.pending} onclick={()=>check.run(main)}>{listenLabel(main.validation)}</Button>
        {:else if mode==='undecided'}
          <p class="notice">{t('sUndecidedHint')}</p><p>{t('sqUndecidedGrow')}</p>
          <div class="actions"><Button variant="primary" href={modelHref}>{t('sqGrow')}</Button><Button href={back}>{t('sqStopHere')}</Button></div>
          <p class="muted">{t('sqStopNote')}</p>
        {:else if mode==='grow'}
          <p>{t(noAI?'sqNoAI':'sqNoDetection')} {t('sqGrowHint')}</p>
          <Button variant="primary" href={modelHref}>{t('sqGrow')}</Button>
        {:else if step===1&&settled==='not_started'}
          <p class="muted">{t(searchFirst?'sqLaterSearch':'sqLater')}</p>
          {#if !searchFirst&&required}<Button pending={check.pending} onclick={()=>check.run(required)}>{t('sqCheckAnswers')}</Button>{/if}
        {:else if step===0&&settled==='not_started'}<p class="muted">{t('sqSkippedNote')}</p>
          {#if required&&!undetected(required)}<Button pending={check.pending} onclick={()=>check.run(required)}>{t('sqCheckAnyway')}</Button>{/if}{/if}
        {#if step===2&&others.length}<details class="more"><summary>{t('sqOtherAIs')}</summary>{#each others as item (item.key)}{@render checkRow(item)}{/each}</details>{/if}
        <!-- The chart is the answer to ② once a cut-off is decided; before that it is only for reference. -->
        {#if settled==='decided'}{@render figure(result)}
        {:else if step===2&&result.checks.length}<details class="more"><summary>{t('sqReference')}</summary>{@render figure(result)}</details>{/if}
      </section>
    </div>
    {#if step===0}<section class="panel done" aria-labelledby="done-title"><h2 id="done-title">{t('sqDone')}</h2>
      <p>{fill('sqDoneHint',{button:t('wExport')})}</p><Button variant="primary" href={back}>{t('uBackResults')}</Button></section>{/if}
    <!-- Deleting is kept away from the listening buttons, in one place at the bottom. -->
    {#if result.search||result.presence.status==='not_found'||result.checks.some(item=>item.validation)}<details class="more redo"><summary>{t('sqRedo')}</summary>
      {#if result.presence.status==='not_found'}<div class="stack">
        <Button variant="danger" size="sm" onclick={()=>confirming=confirming==='miss'?'':'miss'}>{t('sqDeleteMiss')}</Button>
        {#if confirming==='miss'}<div class="confirm" tabindex="-1" use:reveal role="group" aria-label={t('sqDeleteMiss')}><p>{t('sqDeleteMissConfirm')}</p>
          <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeMiss.pending} onclick={()=>removeMiss.run()}>{t('sDeleteYes')}</Button></div></div>{/if}
      </div>{/if}
      {#each result.checks.filter(item=>item.validation) as item (item.key)}<div class="stack">
        <Button variant="danger" size="sm" onclick={()=>confirming=confirming===item.key?'':item.key}>{t('sDeleteCheck')}：{checkName(item)}</Button>
        {#if confirming===item.key}<div class="confirm" tabindex="-1" use:reveal role="group" aria-label={t('sDeleteCheck')}><p>{fill('sDeleteCheckConfirm',{model:checkName(item)})}</p>
          <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeCheck.pending} onclick={()=>removeCheck.run(item)}>{t('sDeleteYes')}</Button></div></div>{/if}
      </div>{/each}
      {#if result.search}<div class="stack">
        <Button variant="danger" size="sm" pending={askSearchDelete.pending} onclick={()=>confirming==='search'?confirming='':askSearchDelete.run()}>{t('sSearchDelete')}</Button>
        {#if confirming==='search'&&preview}<div class="confirm" tabindex="-1" use:reveal role="group" aria-label={t('sSearchDelete')}>
          {#if preview.custom_models}<p class="notice"><strong>{t('sSearchDeleteModels')}</strong></p>{/if}
          {#if preview.marks}<p>{t('sSearchDeleteConfirm')}</p><ul><li>{fill('sSearchDeleteMarks',{n:preview.marks})}</li></ul><p>{t('sSearchDeletePresence')}</p>
          {:else if !preview.custom_models}<p>{t('sSearchDeleteNothing')}</p>{/if}
          {#if preview.marks||preview.custom_models}<p class="muted">{t('sSearchDeleteKept')}</p>{/if}
          <div class="actions"><Button onclick={()=>confirming=''}>{t('mCancel')}</Button><Button variant="danger" pending={removeSearch.pending} onclick={()=>removeSearch.run()}>{t('sSearchDelete')}</Button></div></div>{/if}
      </div>{/if}</details>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('sPreparing')}</p>{/if}
</div>
<style>.steps{margin-top:16px}.step-card{display:flex;flex-direction:column;gap:8px}
  /* The open step: thicker border and a filled number; finished and later steps get a plain number. */
  .step-card.open{border:2px solid rgb(var(--primary-700))}.step-card:not(.open)::before{background:rgb(var(--stone-300));color:rgb(var(--stone-900))}
  .step-head{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:space-between;gap:4px 12px}.step-head h2,.step-head p{margin:0}
  .check-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .check-name{display:flex;flex-direction:column}.confirm{padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}.confirm p,.confirm ul{margin:0 0 12px}
  .more{margin-top:8px}.more summary{min-height:44px;padding:10px 0}.stack{margin-top:8px}.redo{margin-top:24px}
  .done{margin-top:16px;border-left:4px solid rgb(var(--color-success))}
  .heard{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .model-tabs{margin:12px 0 4px}section p{margin:8px 0}</style>
