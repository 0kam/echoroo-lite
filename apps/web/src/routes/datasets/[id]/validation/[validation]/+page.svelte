<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import type { Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),sid=Number(page.params.validation);
  let validation=$state<Validation|null>(null),error=$state(''),paused=$state(false),started=$state(false),batch=$state(0);
  function show(value:Validation){validation=value;}
  onMount(()=>{api<Validation>(`/validations/${sid}`).then(value=>{show(value);started=value.counts[value.stage]?.answered>0;}).catch(e=>error=e.message);});
  const answer=createTask(async(verdict:string)=>{if(!validation?.question)return;const phase=validation.stage;
    show(await api<Validation>(`/validations/${sid}/answers`,{item_id:validation.question.id,verdict}));batch++;
    if(validation.stage!==phase||batch%10===0)paused=true;
  });
  const back=$derived(validation?`${resolve(`/datasets/${id}/species/${validation.species.id}`)}?start_date=${validation.start_date}&end_date=${validation.end_date}`:resolve(`/datasets/${id}`));
  const practice=$derived(validation?`${resolve('/quiz')}?species=${validation.species.id}&return=${encodeURIComponent(`/datasets/${id}/validation/${sid}`)}`:'');
</script>
<div class="page validation-page"><div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if validation}
    <h1>{commonName(validation.species,app.locale)} <i class="sci">{validation.species.scientific_name}</i></h1>
    <p>{validation.start_date}–{validation.end_date}</p>
    {#if !validation.applicable}<p class="notice">{t('wScopeChanged')}</p>{/if}
    {#if validation.stage==='complete'}<section class="panel"><QualityPanel {validation}/><Button href={back}>{t('wBackSpecies')}</Button></section>
    {:else if !started}<section class="panel"><h2>{t('wEvaluate')}</h2><p>{t('wValidationPlan')}</p>
      <p>{t('uReviewCount')}: {validation.counts.calibration.total}</p><p>{t('uSavedAlways')}</p><Button variant="primary" onclick={()=>started=true}>{t('wValidationStart')}</Button></section>
    {:else}
      <section class="panel"><h2>{t(validation.stage==='calibration'?'uProgressAdjust':'uProgressEvaluate')}</h2>
        <p>{validation.counts[validation.stage].answered} / {validation.counts[validation.stage].total} · {t('wAnswered')}</p>
        <progress max={validation.counts[validation.stage].total} value={validation.counts[validation.stage].answered}></progress>
        {#if paused}<p class="notice">{t(validation.stage==='evaluation'&&validation.counts.evaluation.answered===0?'wCalibrationDone':'wBatchDone')}</p>
          <Button variant="primary" onclick={()=>paused=false}>{t('wNextBatch')}</Button>
        {:else if validation.question}
          <h3>{t('wQuestion')}</h3>{#key validation.question.id}<AudioRegion source={validation.question.source} fixedRange start={validation.question.start_s} end={validation.question.end_s}/>{/key}
          <div class="actions answers">{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
            <Button variant={verdict==='target'?'primary':'default'} pending={answer.pending} onclick={()=>answer.run(verdict)}>{t(label)}</Button>{/each}</div>
        {/if}
        {#if answer.error}<p class="error" role="alert">{t(answer.error)}</p>{/if}
      </section>
      <div class="actions"><Button href={back}>{t('wPause')}</Button><Button href={practice}>{t('wPractice')}</Button></div>
    {/if}
  {:else if !error}<p><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.validation-page{max-width:900px}h1{margin:18px 0}p{margin:12px 0}progress{width:100%;accent-color:rgb(var(--primary-700));margin:8px 0 22px}.answers{margin-top:18px}</style>
