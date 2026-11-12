<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,siteOf } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { fill,modelName,type LabelRow,type SpeciesAI } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const site=$derived(siteOf(datasetOf(id)));
  const range=new URLSearchParams([...new URL(page.url.hash.slice(1),page.url).searchParams].filter(([key])=>key.endsWith('_date')));
  // `info` holds the counts and the AI; the two lists change only when a new batch is loaded, so rows do not move while answering.
  let info=$state<SpeciesAI|null>(null),batch=$state<LabelRow[]>([]),answered=$state<LabelRow[]>([]),source=$state<SpeciesAI['source']>(null);
  let error=$state(''),round=0,answeredShown=$state(10),confirming=$state(false);
  const fetchState=(query='')=>api<SpeciesAI>(`/datasets/${id}/species/${speciesId}/custom-model?${range}${query}`);
  const next=createTask(async()=>{const value=await fetchState(`&batch=${round}`);
    info=value;batch=value.batch??[];answered=value.answered;source=value.source;answeredShown=10;});
  onMount(async()=>{await next.run();if(next.error)error=next.error;});
  const label=createTask(async(row:LabelRow,value:'target'|'other')=>{
    const saved=await api<{label:LabelRow['label']}>(`/datasets/${id}/species/${speciesId}/labels`,{segment_id:row.segment_id,label:row.label===value?null:value});
    row.label=saved.label;info=await fetchState();
  },{toast:true});
  // The period the server used for this page, so a link without dates still makes the AI for what is shown.
  const build=createTask(async()=>{await api('/custom-models',{dataset_id:id,species_id:speciesId,
    start_date:info!.start_date,end_date:info!.end_date});round=0;await next.run();});
  const another=()=>{round++;void next.run();};
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const remove=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/delete`,{});confirming=false;round=0;await next.run();},{toast:true});
  const model=$derived(info?.model??null);
  const back=$derived(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}${model?.applicable?`&engine=custom&model_id=${model.id}`:''}`);
  const time=(row:LabelRow)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium'}).format(new Date(row.local_time));
  const made=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:site?.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
  const score=(value:number)=>{const text=value.toFixed(2);
    return source==='custom'?fill('mdScoreCustom',{value:text}):source==='search'?fill('pSimilarity',{value:text}):fill('mdScoreBuiltin',{model:modelName(info!.engine),value:text});};
</script>
<div class="page">
  <div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  <div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{t('customModelTitle')}</h1></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if info}
    <p class="species">{commonName(info.species,app.locale)} <i class="sci">{info.species.scientific_name}</i></p>
    {#snippet counts()}<p><strong>{fill('mdCounts',{positive:info!.examples.positive_5s,negative:info!.examples.negative_5s})}</strong>
      <span class="muted">{fill('mdNeed',{n:info!.requirements.positive_5s})}</span></p>{/snippet}
    <section class="panel">
      {@render counts()}
      {#if info.examples.other_answers}<p class="muted">{fill('mdElsewhere',{n:info.examples.other_answers})}</p>{/if}
      <p>{model?fill('mdRound',{n:model.round,date:made(model.created_at)}):t('mdNoAI')}</p>
      {#if model&&!model.applicable}<p class="info">{t(model.reason??'custom_model_scope_changed')}</p>{/if}
    </section>
    {#snippet row(item:LabelRow)}<article class="row">
      <div class="row-head"><span>{time(item)}</span>{#if item.score!=null}<span class="muted">{score(item.score)}</span>{/if}</div>
      <AudioRegion source={{id:item.recording_id,kind:'recording',path:item.path,seconds:item.seconds}} fixedRange compact start={item.start_s} end={item.end_s}>
        {#snippet actions()}<div class="actions" role="group" aria-label={time(item)}>{#each [['target','mdVoice'],['other','mdNoVoice']] as [value,name]}
          <Button active={item.label===value} aria-pressed={item.label===value} pending={label.pending} disabled={next.pending||build.pending} onclick={()=>label.run(item,value as 'target'|'other')}>{item.label===value?'✓ ':''}{t(name)}</Button>{/each}</div>{/snippet}
      </AudioRegion>
    </article>{/snippet}
    <section class="panel"><h2>{t('mdNextTitle')}</h2>
      <!-- Nothing to rank by is a step still to do, not an error. -->
      {#if !source}<p class="info" role="status">{t('custom_search_needed')}</p>
        <Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}`}>{t('sSearchStart')}</Button>
      {:else}
        <p>{source==='custom'?t('mdIntroCustom'):source==='search'?t('mdIntroSearch'):fill('mdIntroBuiltin',{model:modelName(info.engine)})}</p>
        <p class="muted">{t('mdUndo')}</p>
        {#each batch as item (item.segment_id)}{@render row(item)}{:else}<p>{t('mdNoneLeft')}</p>{/each}
      {/if}
      <div class="footer">
        {@render counts()}
        {#if !info.can_train}{#each info.reasons as reason}<p class="info">{t(reason)}</p>{/each}{/if}
        <div class="actions">
          <Button variant="primary" disabled={!info.can_train} pending={build.pending} onclick={()=>build.run()}>{t(model?'mdRebuild':'customModelCreate')}</Button>
          {#if source}<Button disabled={build.pending} pending={next.pending} onclick={another}>{t('mdAnother')}</Button>{/if}
          <Button href={back}>{t('mdDone')}</Button>
        </div>
        {#if build.pending}<p role="status"><Spinner/> {t('customModelPreparing')}</p><Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>{/if}
        {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
        {#if next.error&&!error}<p class="error" role="alert">{t(next.error)}</p>{/if}
      </div>
    </section>
    {#if answered.length}<details class="panel"><summary>{fill('mdAnswered',{n:answered.length})}</summary>
      <p class="muted">{t('mdUndo')}</p>
      {#each answered.slice(0,answeredShown) as item (item.segment_id)}{@render row(item)}{/each}
      {#if answeredShown<answered.length}<Button onclick={()=>answeredShown+=10}>{t('pMore')}</Button>{/if}
    </details>{/if}
    {#if model}<details class="more"><summary>{t('sMore')}</summary>
      <Button variant="danger" size="sm" onclick={()=>confirming=!confirming}>{t('modelDelete')}</Button>
      {#if confirming}<div class="confirm"><p>{t('modelDeleteConfirm')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>confirming=false}>{t('mCancel')}</Button>
        <Button variant="danger" pending={remove.pending} onclick={()=>remove.run(model.id)}>{t('modelDeleteYes')}</Button></div></div>{/if}
    </details>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>section p{margin:12px 0}.species{margin:0}.row{display:flex;flex-direction:column;gap:8px;padding:16px 0;border-top:1px solid rgb(var(--stone-300))}
  .row-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}.footer{border-top:1px solid rgb(var(--stone-300));padding-top:4px}
  details summary{min-height:44px;padding:10px 0;cursor:pointer}.confirm{margin-top:8px;padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}</style>
