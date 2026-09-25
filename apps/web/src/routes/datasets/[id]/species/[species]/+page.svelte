<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName,speciesPage } from '$lib/species';
  import { openExternal } from '$lib/files';
  import { customName,listenLabel,rangeQuery,type CustomModel,type CustomModelReadiness,type SpeciesResult,type Validation } from '$lib/workflow';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),run=$state(initial.get('run_id')||''),modelId=$state(Number(initial.get('model_id'))||0),customDetail=$state<CustomModel|null>(null),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0,about=$state<{ebird_code:string|null;in_quiz:boolean}|null>(null);
  let openEvidence=$state(-1),canRebuild=$state(false);
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
  // The rebuild page is offered once at least 3 "heard" and 3 "not heard" answers exist.
  async function checkMaterials(data:SpeciesResult){if(!data.validation&&!data.searches.length)return;
    try{const session=await api<{id:number}>('/annotations',{dataset_id:id,start_date:data.start_date,end_date:data.end_date});
      const ready=await api<CustomModelReadiness>(`/annotations/${session.id}/custom-model-readiness?species_id=${encodeURIComponent(speciesId)}`);
      canRebuild=ready.answers.target>=ready.requirements.positive_5s&&ready.answers.other>=ready.requirements.negative_5s;}catch{/* The link simply stays hidden. */}}
  onMount(()=>{void load().then(()=>{if(result)void checkMaterials(result);});api<{ebird_code:string|null;in_quiz:boolean}>(`/species/${speciesId}/examples?dataset_id=${id}`).then(value=>about=value).catch(()=>{});});
  const external=$derived(result&&about?speciesPage(result.species,about.ebird_code,app.locale):null);
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):initial.toString());
  // Creates or resumes the check; the server prepares the site's shared first clips itself.
  const validate=createTask(async()=>{
    if(!result)return;
    const validation=result.validation?.applicable?result.validation:await api<Validation>('/validations',modelId?{model_id:modelId}:{dataset_id:id,species_id:speciesId,start_date:result.start_date,end_date:result.end_date,run_id:run?Number(run):null});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${range}&engine=${engine}${run?`&run_id=${run}`:''}${modelId?`&model_id=${modelId}`:''}`)}`);
</script>
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}?${range}`}>{t('uBackResults')}</a><span>{result?.site.name}</span></div>
    <h1>{result?commonName(result.species,app.locale):t('species')} <i class="sci">{result?.species.scientific_name}</i></h1>
    {#if external}<Button size="sm" onclick={()=>void openExternal(external.url)}>{t('refLearn').replace('{site}',external.name)}</Button>{/if}</div>
    <Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}`}>{t('wFurther')}</Button></div>
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
    {#if result.annotations.length}<section class="panel"><h2>{t('aExactEvidence')}</h2><p>{t('aEvidenceNote')}</p>
      {#each result.annotations as item,index}<div><Button active={openEvidence===index} onclick={()=>openEvidence=openEvidence===index?-1:index}>{item.local_time} · {item.start_s.toFixed(2)}–{item.end_s.toFixed(2)} s</Button>
        {#if openEvidence===index}<div class="evidence"><AudioRegion source={{id:item.recording_id,kind:'recording',path:item.source_path,seconds:item.seconds}} fixedRange start={item.start_s} end={item.end_s}/></div>{/if}
      </div>{/each}</section>{/if}
    <section class="panel"><QualityPanel validation={result.validation}/>
      {#if customDetail&&(!customDetail.applicable||customDetail.status==='threshold_unavailable')}<p class="notice">{t(customDetail.applicable?'custom_threshold_unavailable':customDetail.reason??'custom_model_scope_changed')}</p>{/if}
      {#if !modelId||customDetail?.status==='ready'&&customDetail.applicable}<div class="actions"><Button variant="primary" pending={validate.pending} onclick={()=>validate.run()}>{listenLabel(result.validation)}</Button>
      {#if about?.in_quiz}<Button href={practice}>{t('wPractice')}</Button>{/if}</div>{/if}
    </section>
    {#if canRebuild||result.custom_models?.length}<div class="custom-entry"><Button href={`${resolve(`/datasets/${id}/species/${speciesId}/model`)}?${range}`}>{t('customModelTitle')}</Button></div>{/if}
    {#if result.searches.length}<section class="panel"><h2>{t('wConfirmed')}</h2>
      {#each result.searches as search}<p><a class="button" href={`${resolve(`/datasets/${id}/search/${search.id}`)}?${range}`}>{t('wContinue')} · {search.created_at}</a></p>{/each}</section>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.model-tabs{margin-bottom:24px}section p{margin:16px 0}.evidence{padding:12px}.custom-entry{margin-top:12px}</style>
