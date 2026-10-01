<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { customName,fill,type CustomModel,type CustomModelReadiness,type SearchHit,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const range=new URLSearchParams([...new URL(page.url.hash.slice(1),page.url).searchParams].filter(([key])=>key.endsWith('_date')));
  const draftKey=`echoroo-model:${id}:${speciesId}:${range}`;
  let ready=$state<CustomModelReadiness|null>(null),error=$state(''),confirming=$state<number|null>(null),shown=$state(10);
  try{shown=Number(sessionStorage.getItem(draftKey))||10;}catch{/* The list position is only a convenience. */}
  $effect(()=>{try{sessionStorage.setItem(draftKey,String(shown));}catch{/* Private windows may refuse storage. */}});
  async function load(){
    try{ready=await api<CustomModelReadiness>(`/datasets/${id}/species/${speciesId}/custom-model?${range}&limit=${Math.max(30,shown)}`);}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}
  }
  onMount(()=>void load());
  const label=createTask(async(hit:SearchHit,value:'target'|'other')=>{
    await api(`/search-runs/${ready!.run_id}/labels`,{segment_id:hit.segment_id,label:hit.label===value?null:value});await load();
  },{toast:true});
  const build=createTask(async()=>{if(!ready)return;await api('/custom-models',{dataset_id:id,species_id:speciesId,
    start_date:range.get('start_date'),end_date:range.get('end_date')});await load();});
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const check=createTask(async(model:CustomModel)=>{
    const validation=await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,start_date:model.scope.start_date,end_date:model.scope.end_date,model:'custom',model_id:model.id});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const remove=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/delete`,{});confirming=null;await load();},{toast:true});
  const latest=$derived(ready?.models.find(model=>model.applicable));
  const back=$derived(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}${latest?`&engine=custom&model_id=${latest.id}`:''}`);
  const time=(hit:SearchHit)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium'}).format(new Date(hit.local_time));
</script>
<div class="page">
  <div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  <h1>{t('customModelTitle')}</h1>
  <!-- A missing search is a step still to do, not an error. -->
  {#if error}<p class={error==='custom_search_needed'?'info':'error'} role={error==='custom_search_needed'?'status':'alert'}>{t(error)}</p>{#if error==='custom_search_needed'}<Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}`}>{t('sSearchStart')}</Button>{/if}{/if}
  {#if ready}
    <p class="species">{commonName(ready.species,app.locale)} <i class="sci">{ready.species.scientific_name}</i></p>
    <p>{t('customModelIntro')}</p>
    <section class="panel"><h2>{t('pSourceSearch')}</h2>
      <p><strong>{fill('mdCounts',{positive:ready.examples.positive_5s,negative:ready.examples.negative_5s,checks:ready.examples.negative_from_checks})}</strong></p>
      <p class="muted">{fill('mdNeed',{n:ready.requirements.positive_5s})}</p>
      {#each ready.hits.slice(0,shown) as hit,index (hit.segment_id)}<article class="row">
        <div class="row-head"><span class="rank">{index+1}</span><span>{time(hit)}</span><span class="muted">{fill('pSimilarity',{value:hit.similarity.toFixed(2)})}</span></div>
        <AudioRegion source={{id:hit.recording_id,kind:'recording',path:hit.path,seconds:hit.seconds}} fixedRange compact start={hit.start_s} end={hit.end_s}>
          {#snippet actions()}<div class="actions" role="group" aria-label={t('customModelTitle')}>{#each [['target','mdVoice'],['other','mdNoVoice']] as [value,name]}
            <Button active={hit.label===value} aria-pressed={hit.label===value} pending={label.pending} onclick={()=>label.run(hit,value as 'target'|'other')}>{hit.label===value?'✓ ':''}{t(name)}</Button>{/each}</div>{/snippet}
        </AudioRegion>
      </article>{/each}
      {#if shown<ready.hit_count}<Button onclick={()=>{shown+=10;if(shown>ready!.hits.length)void load();}}>{t('pMore')}</Button>{/if}
    </section>
    <section class="panel">
      {#if ready.can_train}<Button variant={latest?'default':'primary'} pending={build.pending} onclick={()=>build.run()}>{t('customModelCreate')}</Button>
        {#if build.pending}<Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button><p role="status"><Spinner/> {t('customModelPreparing')}</p>{/if}
      {:else}{#each ready.reasons as reason}<p class="info">{t(reason)}</p>{/each}{/if}
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
    </section>
    {#if ready.models.length}<section class="panel"><h2>{t('modelList')}</h2>
      {#each ready.models as model (model.id)}<div class="model-row"><div><strong>{customName(model)}</strong>
          <div class="muted">{model.applicable?fill('mdCounts',{positive:model.training.positive_5s,negative:model.training.negative_5s,checks:model.training.negative_from_checks}):t(model.reason??'custom_model_scope_changed')}</div></div>
        <div class="actions">{#if model.applicable}<Button variant="primary" pending={check.pending} onclick={()=>check.run(model)}>{t('mdCheck')}</Button>{/if}
          <Button variant="ghost" onclick={()=>confirming=confirming===model.id?null:model.id}>{t('modelDelete')}</Button></div>
        {#if confirming===model.id}<div class="confirm"><p>{t('modelDeleteConfirm')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>confirming=null}>{t('mCancel')}</Button><Button variant="danger" pending={remove.pending} onclick={()=>remove.run(model.id)}>{t('modelDeleteYes')}</Button></div></div>{/if}
      </div>{/each}
    </section>{/if}
    <div class="actions"><Button href={back}>{t('wBackSpecies')}</Button></div>
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>section p{margin:12px 0}.species{margin:0}.row{display:flex;flex-direction:column;gap:8px;padding:16px 0;border-top:1px solid rgb(var(--stone-300))}
  .row-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}.rank{font-weight:700}
  .model-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:12px 0;border-top:1px solid rgb(var(--stone-200))}
  .confirm{flex-basis:100%;padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}</style>
