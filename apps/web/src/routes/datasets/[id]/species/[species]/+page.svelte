<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { rangeQuery,type SpeciesResult,type Validation } from '$lib/workflow';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReportView from '$lib/ReportView.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const initial=new URL(page.url.hash.slice(1),page.url).searchParams;
  let result=$state<SpeciesResult|null>(null),engine=$state(initial.get('engine')||'auto'),run=$state(''),error=$state(''),loading=$state(true),liveUrl=$state('');
  let request=0;
  async function load(){const version=++request;loading=true;error='';
    const query=new URLSearchParams({engine});
    if(result){query.set('start_date',result.start_date);query.set('end_date',result.end_date);}
    else for(const key of ['start_date','end_date']){const value=initial.get(key);if(value)query.set(key,value);}
    if(run)query.set('run_id',run);
    const url=`/datasets/${id}/species/${speciesId}?${query}`;
    try{const data=await api<SpeciesResult>(url);if(version===request){result=data;engine=data.report?.engine??engine;liveUrl=url;}}
    catch(e){if(version===request)error=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===request)loading=false;}
  }
  onMount(()=>{void load();});
  const range=$derived(result?rangeQuery(result.start_date,result.end_date):initial.toString());
  const validate=createTask(async()=>{
    if(!result)return;
    const validation=result.validation?.applicable?result.validation:await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,start_date:result.start_date,end_date:result.end_date});
    await goto(resolve(`/datasets/${id}/validation/${validation.id}`));
  },{toast:true});
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}?${range}`)}`);
</script>
<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}?${range}`}>{t('uBackResults')}</a><span>{result?.site.name}</span></div>
    <h1>{result?commonName(result.species,app.locale):t('species')} <i class="sci">{result?.species.scientific_name}</i></h1></div>
    <Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}`}>{t('wFurther')}</Button></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if result}
    <section class="panel">
      <div class="actions model-tabs">{#each result.models as model}<Button active={!run&&engine===model.engine} aria-pressed={!run&&engine===model.engine} disabled={loading} onclick={()=>{engine=model.engine;run='';load();}}>{model.engine==='birdnet'?'BirdNET':'Perch'}</Button>{/each}
        {#if result.searches.length}<Button active={!!run} disabled={loading} onclick={()=>{run=String(result!.searches[0].id);load();}}>{t('wConfirmed')}</Button>{/if}</div>
      {#if loading}<p role="status"><Spinner/> {t('loading')}</p>
      {:else if result.report}{#key liveUrl}<ReportView report={result.report} {liveUrl}/>{/key}
      {:else}<p class="empty">{t('wNoModel')}</p>{/if}
    </section>
    <section class="panel"><QualityPanel validation={result.validation}/>
      <div class="actions"><Button variant="primary" pending={validate.pending} onclick={()=>validate.run()}>{t(result.validation?.stage==='complete'?'wValidationDone':result.validation?.applicable?'wContinue':'wEvaluate')}</Button>
      <Button href={practice}>{t('wPractice')}</Button></div>
    </section>
    {#if result.searches.length}<section class="panel"><h2>{t('wConfirmed')}</h2>
      {#each result.searches as search}<p><a class="button" href={resolve(`/datasets/${id}/search/${search.id}`)}>{t('wContinue')} · {search.created_at}</a></p>{/each}</section>{/if}
  {:else if loading}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.model-tabs{margin-bottom:24px}section p{margin:16px 0}</style>
