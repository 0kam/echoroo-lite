<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { onMount,untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { rangeQuery,type Overview,type Validation } from '$lib/workflow';
  import type { AnnotationSession,CallRange } from '$lib/annotations';
  import RangeEditor from '$lib/RangeEditor.svelte';
  import ValidationSteps from '$lib/ValidationSteps.svelte';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id);
  let session=$state<AnnotationSession|null>(null),ranges=$state<CallRange[]>([]),palette=$state<Species[]>([]),view=$state<'listen'|'list'>('listen');
  let detectionCounts=$state<Record<string,number>>({});
  let existingValidations=$state<{id:number;species:Species[];complete:boolean}[]>([]);
  // `focus` is the species whose reliability check brought the user here; it sets the return path.
  let focus=$state(''),error=$state(''),storageError=$state(false),selected=$state<Species|null>(null),selectedIds=$state<string[]>([]),editor=$state<RangeEditor>();
  const key=$derived(session?.question?`echoroo-annotation:${session.id}:${session.question.id}`:'');
  const previous=$derived(session?.history.filter(i=>i.position<(session?.question?.position??Infinity)).at(-1));
  const query=$derived(session?rangeQuery(session.start_date,session.end_date):'');
  const exit=$derived(focus?`${resolve(`/datasets/${id}/species/${focus}`)}?${query}`:`${resolve(`/datasets/${id}`)}?${query}`);
  // Clips are saved in sets of ten; show progress toward the current set rather than the whole sample.
  const goal=$derived(session?Math.min(session.total,Math.max(10,Math.ceil(((session.question?.position??session.total-1)+1)/10)*10)):0);
  const needed=$derived(session?Math.max(0,Math.min(10,session.total)-session.completed):0);
  function show(data:AnnotationSession){editor?.stop();session=data;ranges=data.question?.ranges??[];palette=data.palette;
    if(data.question)try{const saved=JSON.parse(localStorage.getItem(`echoroo-annotation:${data.id}:${data.question.id}`)||'null');
      if(saved?.version===data.question.version){ranges=saved.ranges;palette=saved.palette;}}catch{storageError=true;}
  }
  $effect(()=>{const current=session?.question,entries=$state.snapshot(ranges),names=$state.snapshot(palette),draftKey=key;
    if(!current||!draftKey)return;untrack(()=>{try{localStorage.setItem(draftKey,JSON.stringify({version:current.version,ranges:entries,palette:names}));storageError=false;}catch{storageError=true;}});
  });
  onMount(()=>{void(async()=>{try{
    const params=new URL(page.url.hash.slice(1)||page.url.pathname+page.url.search,page.url).searchParams;
    const firstParam=params.get('start_date'),lastParam=params.get('end_date');
    const overview=await api<Overview>(`/datasets/${id}/overview${firstParam&&lastParam?'?'+rangeQuery(firstParam,lastParam):''}`);
    const first=overview.start_date,last=overview.end_date;
    detectionCounts=Object.fromEntries(overview.species.map(row=>[row.species.id,Math.max(0,...row.models.map(model=>model.candidate_windows))]));
    const data=await api<AnnotationSession>('/annotations',{dataset_id:id,start_date:first,end_date:last});show(data);
    const grouped=new Map<number,{id:number;species:Species[];complete:boolean}>();
    for(const row of overview.species){const validation=row.validation;if(!validation?.applicable||validation.annotation_session_id!==data.id)continue;
      const validationId=validation.group_ids?.[0]??validation.id,current=grouped.get(validationId);
      if(current){current.species.push(row.species);current.complete=current.complete&&validation.stage==='complete';}
      else grouped.set(validationId,{id:validationId,species:[row.species],complete:validation.stage==='complete'});
    }existingValidations=[...grouped.values()];
    const taxon=params.get('species');if(taxon){selectedIds=[taxon];focus=taxon;}
    if(!data.question||params.get('view')==='list'&&(data.can_evaluate||!data.has_holdout))view='list';
  }catch(e){error=e instanceof Error?e.message:'unknown_error';}})();});
  const navigate=createTask(async(item?:number)=>{if(!session)return;show(await api<AnnotationSession>(`/annotations/${session.id}${item?`?item_id=${item}`:''}`));view='listen';});
  const save=createTask(async()=>{if(!session?.question)return;editor?.stop();const current=session.question;
    const value=await api<AnnotationSession>(`/annotations/${session.id}/answers`,{item_id:current.id,version:current.version,ranges,palette_ids:palette.map(s=>s.id),complete:true});
    try{localStorage.removeItem(key);}catch{storageError=true;}show(value);if(!value.question||value.completed%10===0)view='list';
  });
  const quality=createTask(async()=>{if(!session)return;const speciesIds=[...new Set([...selectedIds,...(selected?[selected.id]:[])])];if(!speciesIds.length)return;const result=await api<Validation>('/validations',{
    dataset_id:id,species_id:speciesIds.length===1?speciesIds[0]:undefined,species_ids:speciesIds.length>1?speciesIds:undefined,start_date:session.start_date,end_date:session.end_date,annotation_session_id:session.id});
    await goto(resolve(`/datasets/${id}/validation/${result.id}`));
  });
  const busy=$derived(save.pending||navigate.pending||quality.pending);
</script>
<div class="page annotation-page">
  <div class="crumbs"><a href={exit}>{t(focus?'wBackSpecies':'uBackResults')}</a><span>›</span>{session?.site.name}</div>
  {#if error}<p class="error" role="alert">{t(error)}</p>
  {:else if session}
    <div class="page-header"><div><h1>{t(view==='list'?'aHeard':'aChoose')}</h1><p>{view==='list'?session.start_date+' — '+session.end_date:t('aProgress').replace('{n}',String(session.question?session.question.position+1:session.total)).replace('{total}',String(goal))}{#if view==='listen'&&session.question} · {new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:session.site.timezone}).format(new Date(session.question.local_time))}{/if}</p></div>
      {#if view==='listen'}<Button onclick={()=>{editor?.stop();view='list';}}>{t('aReviewShared')}</Button>{/if}</div>
    {#if focus}<ValidationSteps validation={null} listening/>{/if}
    {#if !session.applicable}<p class="notice">{t('wScopeChanged')}</p>{/if}
    {#if session.completed<Math.min(10,session.total)}<p class="muted">{t('aWorkflowIntro').replace('{count}',String(Math.min(10,session.total)))}</p>{/if}
    {#if view==='listen'&&session.question}
      <section class="panel listen-panel">
        {#key session.question.id}<RangeEditor bind:this={editor} source={session.question.source} start={session.question.start_s} end={session.question.end_s}
          bind:palette bind:ranges {detectionCounts} storageKey={key} disabled={busy}/>{/key}
        <div class="footer"><Button disabled={busy||!previous} onclick={()=>previous&&navigate.run(previous.id)}>‹ {t('aPrevious')}</Button>
          <Button variant="primary" pending={save.pending} disabled={busy} onclick={()=>save.run()}>{t(session.question.position===session.total-1?'aFinish':'aNext')}</Button></div>
        <p class:notice={storageError} class="muted" role="status">{t(storageError?'aStorageFailed':'aDraft')}</p>
      </section>
      <p class="muted">{t('aPaletteHint')}</p>
    {:else}
      <p>{t('aSaved').replace('{count}',String(session.completed))}</p>
      <div class="actions">{#if session.question}<Button variant={session.can_evaluate?'default':'primary'} onclick={()=>navigate.run()}>{t('aContinue')}</Button>{/if}{#if previous}<Button onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
      {#snippet heard()}<section class="panel"><h2>{t('aExactEvidence')}</h2><p class="muted">{t('aEvidenceNote')}</p>
        {#each session!.heard as row}<div class="animal"><a href={`${resolve(`/datasets/${id}/species/${row.species.id}`)}?${query}`}><strong>{commonName(row.species,app.locale)||row.species.scientific_name}</strong>{#if commonName(row.species,app.locale)}<i>{row.species.scientific_name}</i>{/if}</a><span>{t('aIntervals').replace('{count}',String(row.intervals))}</span></div>
        {:else}<p>{t('aNoHeard')}</p>{/each}
      </section>{/snippet}
      {#snippet check()}<section class="panel"><h2>{t('aQualityTitle')}</h2><p>{t('aQualityHelp')}</p>
        {#if existingValidations.length}<div class="existing-validations">{#each existingValidations as validation}<a class="button" href={resolve(`/datasets/${id}/validation/${validation.id}`)}><span>{validation.species.map(species=>commonName(species,app.locale)||species.scientific_name).join('・')}</span><strong>{t(validation.complete?'vViewExisting':'vResumeExisting')}</strong></a>{/each}</div>{/if}
        {#if !session!.has_holdout}<p>{t('annotation_no_holdout')}</p>{:else if !session!.can_evaluate}<p>{t('aNeedBatch').replace('{count}',String(needed))}</p>{:else}
          <fieldset class="species-checks"><legend>{t('vChooseSpecies')}</legend>{#each [...palette].sort((a,b)=>(detectionCounts[b.id]??0)-(detectionCounts[a.id]??0)||(commonName(a,app.locale)||a.scientific_name).localeCompare(commonName(b,app.locale)||b.scientific_name,app.locale)) as s}<label><input type="checkbox" value={s.id} bind:group={selectedIds} disabled={busy}/> {commonName(s,app.locale)||s.scientific_name}</label>{/each}</fieldset>
          <p class="muted">{t('vSharedSpeciesHint')}</p>
          <details><summary>{t('aAddSpecies')}</summary><SpeciesPicker bind:value={selected} label={t('aCatalog')} disabled={busy}/><p class="muted">{t('vAddedSpeciesIncluded')}</p></details>
          <div class="actions"><Button variant="primary" disabled={(!selectedIds.length&&!selected)||busy||!session!.applicable} pending={quality.pending} onclick={()=>quality.run()}>{t('aQualityStart')}</Button></div>
        {/if}
      </section>{/snippet}
      {#if focus}{@render check()}{@render heard()}{:else}{@render heard()}{@render check()}{/if}
    {/if}
    {#if save.error||navigate.error||quality.error}<p class="error" class:answer-feedback={view==='listen'&&!!session.question} role="alert">{t(save.error||navigate.error||quality.error)}</p>{/if}
  {:else}<h1>{t('aListen')}</h1><p><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.answer-feedback{position:fixed;bottom:76px;left:28px;right:28px;z-index:4;padding:12px;background:rgb(var(--color-page-bg));box-shadow:0 0 0 1px currentColor;border-radius:8px;}:global(.main:has(.annotation-page)){padding-top:8px}.page.annotation-page{max-width:1280px;gap:6px;padding-bottom:90px}.annotation-page .crumbs{margin-bottom:0}.annotation-page .crumbs a{padding:3px 12px}.annotation-page .listen-panel{margin-top:0;padding:12px}.annotation-page .listen-panel .footer{position:fixed;bottom:0;left:0;right:0;z-index:3;margin:0;padding:12px max(28px,calc((100vw - 1200px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300))}.annotation-page h1{font-size:27px}.page-header{margin-bottom:0}.page-header>div{display:flex;align-items:baseline;flex-wrap:wrap;gap:4px 16px}.footer{display:flex;justify-content:space-between;gap:12px;margin:20px 0 12px}.animal{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:12px 0;border-top:1px solid rgb(var(--stone-200))}.animal a{color:inherit}.animal i{display:block;font-size:13px}.panel{margin-top:20px}.panel>.actions{margin-top:16px}.panel>p{margin:12px 0}.existing-validations{display:grid;gap:8px;margin:12px 0}.existing-validations a{display:flex;justify-content:space-between;gap:16px;text-align:left}.species-checks{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:8px 16px;max-height:280px;overflow:auto;padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px}.species-checks legend{font-weight:700}.species-checks label{min-height:36px}</style>
