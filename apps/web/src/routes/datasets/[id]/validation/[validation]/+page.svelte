<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { validationPhase,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReferencePanel,{type ExampleRef} from '$lib/ReferencePanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),sid=Number(page.params.validation);
  let validation=$state<Validation|null>(null),error=$state(''),paused=$state(false),started=$state(false),saved=$state(false),heard=$state<ExampleRef[]>();
  const phase=$derived(validationPhase(validation));
  const previous=$derived(validation?.history.filter(item=>item.position<(validation?.question?.position??Infinity)).at(-1));
  onMount(()=>{api<Validation>(`/validations/${sid}`).then(value=>{validation=value;started=value.stage!=='calibration'||value.counts.calibration.answered>0;const active=validationPhase(value);
    paused=active!=='calibration'&&value.counts[active].answered>0&&value.counts[active].answered%10===0;
  }).catch(e=>error=e.message);});
  const navigate=createTask(async(itemId?:number)=>{validation=await api<Validation>(`/validations/${sid}${itemId?`?item_id=${itemId}`:''}`);paused=false;started=true;saved=false;});
  const begin=createTask(async()=>{if(!validation?.applicable)return;validation=await api<Validation>(`/validations/${sid}/evaluation`,{});paused=false;});
  const beginRecall=createTask(async()=>{if(!validation?.applicable)return;validation=await api<Validation>(`/validations/${sid}/recall`,{});paused=false;started=true;});
  const answer=createTask(async(verdict:string)=>{if(!validation?.applicable||!validation.question)return;const question=validation.question;
    const value=await api<Validation>(`/validations/${sid}/answers`,{item_id:question.id,verdict,version:question.version,examples_snapshot:heard});
    saved=question.verdict!==null;
    validation=saved?await api<Validation>(`/validations/${sid}?item_id=${question.id}`):value;
    if(!saved&&value.counts[phase].answered>0&&value.counts[phase].answered%10===0)paused=true;
  });
  const busy=$derived(answer.pending||navigate.pending||begin.pending||beginRecall.pending);
  const back=$derived(validation?`${resolve(`/datasets/${id}/species/${validation.species.id}`)}?start_date=${validation.start_date}&end_date=${validation.end_date}${validation.run_id?`&run_id=${validation.run_id}`:''}${validation.model_id?`&model_id=${validation.model_id}&engine=custom`:''}`:resolve(`/datasets/${id}`));
  const practice=$derived(validation?`${resolve('/quiz')}?species=${validation.species.id}&return=${encodeURIComponent(`/datasets/${id}/validation/${sid}`)}`:'');
  // precision_audit.SAMPLE_SIZE: each AI has its own fixed sample of up to 30 sounds.
  const overview=$derived(validation?.method==='fixed_precision_audit_v1'?t('refOverview').replace('{n}',String(validation.counts.calibration.total)).replace('{m}','30'):'');
  const shared=$derived(!!validation?.annotation_session_id);
  const sharedProgress=$derived(validation?t('aSharedProgress').replace('{answered}',String(validation.counts.calibration.answered)).replace('{remaining}',String(validation.counts.calibration.total-validation.counts.calibration.answered)):'');
</script>
{#snippet examples()}{#if validation}<div class="examples-column"><ReferencePanel species={validation.species} datasetId={id} validationId={sid} {practice} onexamples={list=>heard=list}/></div>{/if}{/snippet}
<div class="page validation-page"><div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if validation}
    <h1>{commonName(validation.species,app.locale)} <i class="sci">{validation.species.scientific_name}</i></h1>
    {#if !started||!validation.question}<p>{validation.start_date}–{validation.end_date}</p>{/if}
    {#if !validation.applicable}<p class="notice">{t(validation.model_reason??(validation.annotation_source_current?'wScopeChanged':'annotation_source_changed'))}</p>{/if}
    {#if validation.stage==='complete'&&!validation.question}<section class="panel"><QualityPanel {validation}/>
      {#if validation.can_start_recall}<p>{t('vRecallOptional')}</p>{/if}
      <div class="actions"><Button href={back}>{t('wBackSpecies')}</Button>{#if validation.can_start_recall}<Button pending={beginRecall.pending} disabled={busy||!validation.applicable} onclick={()=>beginRecall.run()}>{t('vStartRecall')}</Button>{/if}{#if previous}<Button pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
      {#if beginRecall.error}<p class="error" role="alert">{t(beginRecall.error)}</p>{/if}</section>
    {:else if !started}<section class="panel"><h2>{t('wEvaluate')}</h2><p>{t(validation.kind==='search'?'rSearchValidationPlan':'wValidationPlan')}</p>
      {#if shared&&validation.counts.calibration.answered}<p>{sharedProgress}</p>{/if}
      <p>{overview||`${t('uReviewCount')}: ${validation.counts.calibration.total}`}</p><p>{t('uSavedAlways')}</p>
      <p><strong>{t('refListenFirst')}</strong></p>{@render examples()}<Button variant="primary" onclick={()=>started=true}>{t('wValidationStart')}</Button></section>
    {:else}
      <div class="with-examples"><section class="panel"><div class="phase-progress"><h2>{t(phase==='recall'?'vRecallTitle':validation.kind==='custom'?'customModelEvaluation':phase==='calibration'&&shared?'aSharedAdjustment':phase==='calibration'?'uProgressAdjust':'uProgressEvaluate')}</h2>
        <p>{t('vAnsweredCount').replace('{answered}',String(validation.counts[phase].answered)).replace('{total}',String(validation.counts[phase].total)).replace('{remaining}',String(validation.counts[phase].total-validation.counts[phase].answered))}</p></div>
        {#if validation.method==='fixed_precision_audit_v1'&&phase==='evaluation'}<p class="muted">{t('vFixedEvaluationPace').replace('{total}',String(validation.counts.evaluation.total))}</p>
        {:else if phase==='evaluation'}<p class="muted">{t('vEvaluationPace').replace('{total}',String(validation.counts.evaluation.total))}</p>{/if}
        {#if shared&&phase!=='calibration'}<p class="muted">{t('aHoldout')}</p>{/if}
        {#if phase==='calibration'&&!validation.question}
          <p>{t('vCalibrationReady')}</p><p class="muted">{t('vCalibrationFreeze')}</p>
          <Button variant="primary" pending={begin.pending} disabled={busy||!validation.applicable} onclick={()=>begin.run()}>{t('vBeginEvaluation')}</Button>
        {:else if paused}
          {#if phase==='evaluation'}<QualityPanel {validation}/>{:else}<p>{t('wBatchDone')}</p>{/if}
          <Button variant="primary" onclick={()=>paused=false}>{t('wNextBatch')}</Button>
        {:else if validation.question}
          {@const question=validation.question}
          {#key question.id}<AudioRegion source={question.source} fixedRange zoomable start={question.start_s} end={question.end_s} marks={question.ranges.filter(range=>range.species_id===validation?.species.id)} storageKey={`echoroo-validation:${sid}:view`}/>{/key}
          {#if question.verdict}<p role="status">{t(saved?'vCorrectionSaved':'vPreviousAnswer')}: {t(question.verdict==='target'?'wHeard':question.verdict==='other'?'wNotHeard':'wCannotTell')}</p>{/if}
          <div class="actions answers">{#if previous}<Button disabled={busy} pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vPreviousSound')}</Button>{/if}{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
            <Button variant={verdict==='target'?'primary':'default'} aria-pressed={question.verdict===verdict} disabled={busy||!validation.applicable} pending={answer.pending} onclick={()=>answer.run(verdict)}>{t(label)}</Button>{/each}<p class="muted unknown-note">{t('vUnknownNote')}</p></div>
        {/if}
        <div class="actions review-navigation">
          {#if previous&&(!validation.question||paused)}<Button disabled={busy} pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vPreviousSound')}</Button>{/if}
          {#if validation.question?.verdict}<Button disabled={busy} onclick={()=>navigate.run()}>{t(validation.stage==='complete'?'wValidationDone':phase==='calibration'&&validation.counts.calibration.answered===validation.counts.calibration.total?'vBackCalibrationReady':'vResume')}</Button>{/if}
        </div>
        {#if answer.error||navigate.error||begin.error||beginRecall.error}<p class="error" class:answer-feedback={!!validation.question&&!paused} role="alert">{t(answer.error||navigate.error||begin.error||beginRecall.error)}</p>{/if}
      </section>{@render examples()}</div>
      <div class="actions"><Button href={back}>{t('wPause')}</Button></div>
    {/if}
  {:else if !error}<p><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.answer-feedback{position:fixed;bottom:76px;left:28px;right:28px;z-index:4;padding:12px;background:rgb(var(--color-page-bg));box-shadow:0 0 0 1px currentColor;border-radius:8px;}:global(.main:has(.validation-page)){padding-top:16px}.page.validation-page{max-width:1280px;gap:8px;padding-bottom:130px}.with-examples{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,380px);gap:16px;align-items:start}.examples-column{display:flex;flex-direction:column;gap:8px;position:sticky;top:16px}.unknown-note{flex-basis:100%;text-align:center;margin:0;font-size:14px}@media(max-width:950px){.with-examples{grid-template-columns:minmax(0,1fr)}.examples-column{position:static}}.validation-page .crumbs{margin-bottom:0}.validation-page .panel{padding:16px}.validation-page h1{font-size:27px;margin:0}.validation-page .sci{display:inline;font-size:16px;margin-left:12px}.phase-progress{display:flex;align-items:center;justify-content:space-between;gap:12px}.validation-page h2{font-size:20px;margin-bottom:8px}p{margin:6px 0}.answers{position:fixed;bottom:0;left:0;right:0;z-index:3;justify-content:center;padding:12px 24px;background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));margin:0}.review-navigation{margin-top:24px}</style>
