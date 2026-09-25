<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { rangeQuery,type CustomModel,type SpeciesResult,type Validation } from '$lib/workflow';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ValidationSteps from '$lib/ValidationSteps.svelte';
  import CustomModelPanel from '$lib/CustomModelPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),run=$state(initial.get('run_id')||''),modelId=$state(Number(initial.get('model_id'))||0),customDetail=$state<CustomModel|null>(null),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0;
  let openEvidence=$state(-1);
  async function load(){const version=++request;loading=true;error='';
    const query=new URLSearchParams({engine});
    if(result){query.set('start_date',result.start_date);query.set('end_date',result.end_date);}
    else for(const key of ['start_date','end_date']){const value=initial.get(key);if(value)query.set(key,value);}
    if(run)query.set('run_id',run);
    if(modelId)query.set('model_id',String(modelId));
    const url=`/datasets/${id}/species/${speciesId}?${query}`;
    try{const data=await api<SpeciesResult>(url);const returnedModelId=data.report?.model_id??modelId;const detail=returnedModelId?await api<CustomModel>(`/custom-models/${returnedModelId}`):null;if(version===request){result=data;modelId=returnedModelId;customDetail=detail;engine=data.report?.engine??engine;liveUrl=url;}}
    catch(e){if(version===request)error=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===request)loading=false;}
  }
  onMount(()=>{void load();});
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):initial.toString());
  const validate=createTask(async()=>{
    if(!result)return;
    if(modelId){const validation=result.validation?.applicable?result.validation:await api<Validation>('/validations',{model_id:modelId});await goto(resolve(`/datasets/${id}/validation/${validation.id}`));return;}
    if(!run&&!result.validation?.applicable){await goto(`${resolve(`/datasets/${id}/annotations`)}?${range}&species=${speciesId}`);return;}
    const validation=result.validation?.applicable?result.validation:await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,start_date:result.start_date,end_date:result.end_date,run_id:run?Number(run):null});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  // The current view (period and result tab), so pages opened from here can return to it.
  const view=$derived(`${range}&engine=${engine}${run?`&run_id=${run}`:''}${modelId?`&model_id=${modelId}`:''}`);
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${view}`)}`);
  const customName=(model:CustomModel)=>t('customModelNumber').replace('{number}',String(model.id));
  const customPrecision=$derived(customDetail?.cv.reference_precision?.value??null),customRecall=$derived(customDetail?.cv.reference_recall?.value??null);
</script>
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}?${range}`}>{t('uBackResults')}</a><span>{result?.site.name}</span></div>
    <h1>{result?commonName(result.species,app.locale):t('species')} <i class="sci">{result?.species.scientific_name}</i></h1></div>
    <Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${view}`}>{t('wFurther')}</Button></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    {#if result.location_stale}<p class="notice" role="status">{t('location_reanalysis')}</p>{/if}
    <section class="panel">
      <div class="actions model-tabs">{#each result.models as model}<Button active={!run&&!modelId&&engine===model.engine} aria-pressed={!run&&!modelId&&engine===model.engine} disabled={loading} onclick={()=>{engine=model.engine;run='';modelId=0;load();}}>{model.engine==='birdnet'?'BirdNET':'Perch'}</Button>{/each}
        {#each result.custom_models??[] as model}<Button active={!run&&modelId===model.id} aria-pressed={!run&&modelId===model.id} disabled={loading} onclick={()=>{engine='custom';run='';modelId=model.id;load();}}>{customName(model)}</Button>{/each}
        {#if result.searches.length}<Button active={!!run} aria-pressed={!!run} disabled={loading} onclick={()=>{run=String(result!.searches[0].id);modelId=0;load();}}>{t('wConfirmed')}</Button>{/if}</div>
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>
      {:else if result.report}{#key liveUrl}<ReportView report={result.report} {liveUrl}/>{/key}
      {:else}<p class="empty">{t('wNoModel')}</p>{/if}
    </section>
    <section class="panel"><QualityPanel validation={result.validation}/>
      {#if !modelId&&(!result.validation||result.validation.applicable)}<ValidationSteps validation={result.validation}/>{/if}
      {#if !run&&!modelId&&!result.validation?.applicable}<p class="muted">{t('aQualityRouteIntro')}</p>{/if}
      {#if !modelId||customDetail?.status==='ready'&&customDetail.applicable}<div class="actions"><Button variant="primary" pending={validate.pending} onclick={()=>validate.run()}>{t(result.validation&&!result.validation.applicable?'wCheckAgain':result.validation?.stage==='complete'?'vViewQuality':result.validation?.applicable?'wContinue':'wEvaluate')}</Button>
      <Button href={practice}>{t('wPractice')}</Button></div>{/if}
    </section>
    {#if result.annotations.length}<section class="panel"><h2>{t('aExactEvidence')}</h2><p>{t('aEvidenceNote')}</p>
      {#each result.annotations as item,index}<div><Button active={openEvidence===index} onclick={()=>openEvidence=openEvidence===index?-1:index}>{new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:result.site.timezone}).format(new Date(item.local_time))} · {(item.end_s-item.start_s).toFixed(1)} {t('seconds')}</Button>
        {#if openEvidence===index}<div class="evidence"><AudioRegion source={{id:item.recording_id,kind:'recording',path:item.source_path,seconds:item.seconds}} fixedRange start={item.start_s} end={item.end_s}/></div>{/if}
      </div>{/each}</section>{/if}
    {#if customDetail}<section class="panel custom-detail"><h2>{customName(customDetail)}</h2>
      <p><strong>{t('customModelMaterials')}</strong> · {customDetail.training.positive_5s} {t('customModelPositive')} · {customDetail.training.negative_5s} {t('customModelNegative')} · {customDetail.training.recordings} {t('customModelRecordings')}</p>
      {#if !customDetail.applicable}<p class="notice">{t(customDetail.reason??'custom_model_scope_changed')}</p>
      {:else if customDetail.status==='threshold_unavailable'}<p class="notice">{t('custom_threshold_unavailable')}</p>
      {:else}<h3>{t('customModelCv')}</h3><p class="muted">{t('customModelCvHint')}</p>
        {#if customPrecision!==null&&customRecall!==null}
          <p>{t('wPrecision')}: {(customPrecision*100).toFixed(1)}% ({customDetail.cv.reference_precision?.positive} / {customDetail.cv.reference_precision?.predicted})</p>
          <p>{t('wRecall')}: {(customRecall*100).toFixed(1)}% ({customDetail.cv.reference_recall?.found} / {customDetail.cv.reference_recall?.present})</p>
        {/if}
        <h3>{t('customModelEvaluation')}</h3>{#if customDetail.evaluation.validation_id!==null}<p>{customDetail.evaluation.answered} / {customDetail.evaluation.total} {t('wAnswered')}</p>{:else}<p>{t('customModelEvaluationPending')}</p>{/if}{/if}
    </section>{/if}
    <div class="custom-entry"><CustomModelPanel datasetId={id} speciesId={speciesId} startDate={result.start_date} endDate={result.end_date} oncreated={model=>{modelId=model.id;engine='custom';run='';load();}}/></div>
    {#if result.searches.length}<section class="panel"><h2>{t('wConfirmed')}</h2>
      {#each result.searches as search}<p><a class="button" href={`${resolve(`/datasets/${id}/search/${search.id}`)}?${range}`}>{t('wContinue')} · {search.created_at}</a></p>{/each}</section>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.model-tabs{margin-bottom:24px}section p{margin:16px 0}.evidence{padding:12px}.custom-detail h3{font-size:16px;margin:20px 0 4px}.custom-entry{margin-top:12px}</style>
