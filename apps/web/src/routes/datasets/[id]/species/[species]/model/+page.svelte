<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { customName,fill,listenLabel,qualityLines,type CustomModelReadiness,type Overview,type Validation } from '$lib/workflow';
  import type { CallRange } from '$lib/annotations';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import RangeEditor from '$lib/RangeEditor.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Model = CustomModelReadiness['models'][number];
  type Waiting = CustomModelReadiness['needs_range'][number];
  const id=Number(page.params.id),speciesId=page.params.species!;
  const range=new URL(page.url.hash.slice(1),page.url).searchParams;
  const scope:{dataset_id:number;start_date:string|null;end_date:string|null}={dataset_id:id,start_date:range.get('start_date'),end_date:range.get('end_date')};
  let ready=$state<CustomModelReadiness|null>(null),error=$state(''),open=$state(''),marking=$state<number|null>(null),marks=$state<CallRange[]>([]),confirming=$state<number|null>(null);
  async function load(){
    try{if(!scope.start_date||!scope.end_date){const overview=await api<Overview>(`/datasets/${id}/overview`);scope.start_date=overview.start_date;scope.end_date=overview.end_date;}
      const session=await api<{id:number}>('/annotations',scope);
      ready=await api<CustomModelReadiness>(`/annotations/${session.id}/custom-model-readiness?species_id=${encodeURIComponent(speciesId)}`);}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}
  }
  onMount(()=>void load());
  const back=`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}`;
  const time=(value:{local_time:string})=>value.local_time.slice(0,19).replace('T',' ');
  // New marks are saved on the original "heard" answer; other species' marks on that sound are kept.
  const saveMarks=createTask(async(item:Waiting)=>{
    await api<Validation>(`/validations/${item.validation_id}/answers`,{item_id:item.item_id,verdict:'target',version:item.version,
      ranges:[...item.ranges.filter(value=>value.species_id!==speciesId),...marks]});
    marking=null;await load();
  },{toast:true});
  const build=createTask(async()=>{if(!ready)return;await api('/custom-models',{annotation_session_id:ready.annotation_session_id,species_id:speciesId});await load();});
  const check=createTask(async(model:Model)=>{
    const validation=model.validation?.applicable?model.validation:await api<Validation>('/validations',{model_id:model.id});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const remove=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/delete`,{});confirming=null;await load();},{toast:true});
  const usable=(model:Model)=>model.applicable&&model.status==='ready';
  const latest=$derived(ready?.models.find(usable)??ready?.models[0]);
  const status=(model:Model)=>usable(model)?qualityLines(model.validation).join(' / '):t(model.applicable?'custom_threshold_unavailable':model.reason??'custom_model_scope_changed');
</script>
<div class="page">
  <div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  <h1>{t('customModelTitle')}</h1>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if ready}
    <p class="species">{commonName(ready.species,app.locale)} <i class="sci">{ready.species.scientific_name}</i></p>
    <section class="panel"><h2>{t('customModelMaterials')}</h2>
      {#each [['target','customModelPositive'],['other','customModelNegative']] as [label,title]}
        {@const list=ready.materials.filter(item=>item.label===label)}
        <h3>{t(title)}: {list.length}</h3>
        {#each list as item,index}{@const key=`${label}-${index}`}<div class="sound"><Button active={open===key} onclick={()=>open=open===key?'':key}>{time(item)} · {t(item.origin==='search'?'modelFromSearch':'modelFromListening')}</Button>
          {#if open===key}<AudioRegion source={item.source} fixedRange start={item.start_s} end={item.end_s}/>{/if}</div>{/each}
      {/each}
      {#if ready.needs_range.length}<h3>{fill('modelNeedsMarks',{n:ready.needs_range.length})}</h3><p class="muted">{t('modelNeedsMarksHint')}</p>
        {#each ready.needs_range as item,index}<div class="sound">
          {#if marking===index}<RangeEditor source={item.source} start={item.start_s} end={item.end_s} target={ready.species} bind:ranges={marks} storageKey={`echoroo-model-mark:${item.validation_id}:${item.item_id}`} disabled={saveMarks.pending}/>
            <div class="actions"><Button variant="primary" pending={saveMarks.pending} disabled={!marks.length} onclick={()=>saveMarks.run(item)}>{t('modelSaveMarks')}</Button><Button disabled={saveMarks.pending} onclick={()=>marking=null}>{t('mCancel')}</Button></div>
          {:else}<span>{time(item)}</span><Button disabled={saveMarks.pending} onclick={()=>{marking=index;marks=[];}}>{t('modelMark')}</Button>{/if}
        </div>{/each}{/if}
      <p>{t('customModelIntro')}</p>
      {#if ready.can_train}<Button variant={latest?'default':'primary'} pending={build.pending} onclick={()=>build.run()}>{t('customModelCreate')}</Button>
        {#if build.pending}<p role="status"><Spinner/> {t('customModelPreparing')}</p>{/if}
      {:else}{#each ready.reasons as reason}<p class="notice">{t(reason)}</p>{/each}
        {#if ready.reasons.some(reason=>reason.endsWith('_examples_needed'))}<Button href={`${resolve(`/datasets/${id}/search`)}?species=${encodeURIComponent(speciesId)}&${range}`}>{t('customModelFindMore')}</Button>{/if}{/if}
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
    </section>
    {#if latest}<section class="panel"><h2>{customName(latest)}</h2>
      {#if !usable(latest)}<p class="notice">{status(latest)}</p>
      {:else}<p>{fill('modelDetections',{n:(latest.detections??0).toLocaleString(app.locale)})}</p>
        {#each qualityLines(latest.validation) as line}<p><strong>{line}</strong></p>{/each}
        <Button variant="primary" pending={check.pending} onclick={()=>check.run(latest)}>{listenLabel(latest.validation,'modelCheck30')}</Button>
        {#if latest.cv.reference_precision&&latest.cv.reference_recall}<details><summary>{t('customModelCv')}</summary><p class="muted">{t('customModelCvHint')}</p>
          <p>{t('wPrecision')}: {Math.round((latest.cv.reference_precision.value??0)*100)}% ({latest.cv.reference_precision.positive} / {latest.cv.reference_precision.predicted})</p>
          <p>{t('wRecall')}: {Math.round((latest.cv.reference_recall.value??0)*100)}% ({latest.cv.reference_recall.found} / {latest.cv.reference_recall.present})</p></details>{/if}
      {/if}</section>{/if}
    {#if ready.models.length}<section class="panel"><h2>{t('modelList')}</h2>
      {#each ready.models as model}<div class="model-row"><div><strong>{customName(model)}</strong><div class="muted">{status(model)}</div></div>
        {#if confirming===model.id}<p>{t('modelDeleteConfirm')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>confirming=null}>{t('mCancel')}</Button><Button variant="danger" pending={remove.pending} onclick={()=>remove.run(model.id)}>{t('modelDeleteYes')}</Button></div>
        {:else}<Button onclick={()=>confirming=model.id}>{t('modelDelete')}</Button>{/if}</div>{/each}
    </section>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>h3{font-size:16px;margin:20px 0 8px}.sound{display:flex;flex-wrap:wrap;align-items:center;gap:10px;padding:6px 0;border-top:1px solid rgb(var(--stone-200))}.sound>:global(.audio),.sound>:global(div){flex-basis:100%}.model-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-top:1px solid rgb(var(--stone-200))}section p{margin:12px 0}.species{margin:0}</style>
