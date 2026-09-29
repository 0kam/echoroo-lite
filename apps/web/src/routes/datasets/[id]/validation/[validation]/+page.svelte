<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { goalMissed,recallLow,rangeQuery,validationPhase,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ValidationSteps from '$lib/ValidationSteps.svelte';
  import CheckAdvice from '$lib/CheckAdvice.svelte';
  import ReferencePanel,{type ExampleRef} from '$lib/ReferencePanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),sid=Number(page.params.validation);
  let validation=$state<Validation|null>(null),error=$state(''),paused=$state(false),started=$state(false),corrected=$state(false),heard=$state<ExampleRef[]>();
  const phase=$derived(validationPhase(validation));
  const previous=$derived(validation?.history.filter(item=>item.position<(validation?.question?.position??Infinity)).at(-1));
  const next=$derived(validation?.history.find(item=>item.position>(validation?.question?.position??Infinity)));
  // An answered sound always has a next sound: a later answered one, or the first unanswered one (or the finished step).
  const goNext=()=>navigate.run(next?.id);
  const answered=$derived(!!validation?.question?.verdict);
  const remaining=$derived(validation?validation.counts[phase].total-validation.counts[phase].answered:0);
  const finishLabel=$derived(phase==='calibration'?'vBackCalibrationReady':'wValidationDone');
  const nextLabel=$derived(next||remaining>0?'vNextSound':finishLabel);
  onMount(()=>{api<Validation>(`/validations/${sid}`).then(value=>{validation=value;started=value.stage!=='calibration'||value.counts.calibration.answered>0;const active=validationPhase(value);
    paused=active!=='calibration'&&value.counts[active].answered>0&&value.counts[active].answered%10===0;
  }).catch(e=>error=e.message);});
  const navigate=createTask(async(itemId?:number)=>{validation=await api<Validation>(`/validations/${sid}${itemId?`?item_id=${itemId}`:''}`);paused=false;started=true;corrected=false;});
  const begin=createTask(async()=>{if(!validation?.applicable)return;validation=await api<Validation>(`/validations/${sid}/evaluation`,{});paused=false;corrected=false;});
  const beginRecall=createTask(async()=>{if(!validation?.applicable)return;validation=await api<Validation>(`/validations/${sid}/recall`,{});paused=false;started=true;corrected=false;});
  const answer=createTask(async(verdict:string)=>{if(!validation?.applicable||!validation.question)return;const question=validation.question;
    const value=await api<Validation>(`/validations/${sid}/answers`,{item_id:question.id,verdict,version:question.version,examples_snapshot:heard});
    // Correcting an earlier sound moves on to the following sound, just like a first answer.
    corrected=question.verdict!==null;
    if(corrected){validation=next?await api<Validation>(`/validations/${sid}?item_id=${next.id}`):value;return;}
    validation=value;
    if(value.counts[phase].answered>0&&value.counts[phase].answered%10===0)paused=true;
  });
  const busy=$derived(answer.pending||navigate.pending||begin.pending||beginRecall.pending);
  // The species results tab this check belongs to, so pages opened from here return to it.
  const range=$derived(validation?rangeQuery(validation.start_date,validation.end_date):'');
  const view=$derived(validation?`${range}${validation.run_id?`&run_id=${validation.run_id}`:''}${validation.model_id?`&model_id=${validation.model_id}&engine=custom`:''}`:'');
  const back=$derived(validation?`${resolve(`/datasets/${id}/species/${validation.species.id}`)}?${view}`:resolve(`/datasets/${id}`));
  const practice=$derived(validation?`${resolve('/quiz')}?species=${validation.species.id}&return=${encodeURIComponent(`/datasets/${id}/validation/${sid}`)}`:'');
  const shared=$derived(!!validation?.annotation_session_id);
  const sharedProgress=$derived(validation?t('aSharedProgress').replace('{answered}',String(validation.counts.calibration.answered)).replace('{remaining}',String(validation.counts.calibration.total-validation.counts.calibration.answered)):'');
  // One sentence per step on why these sounds are asked; the reasons differ between AI detections and searches.
  const note=$derived(!validation?.question?'':phase==='recall'?'vRecallNote':validation.kind==='search'?(phase==='calibration'?'vSearchFirstNote':'vSearchEvaluationNote'):phase==='evaluation'?'vEvaluationNote':shared?'vFirstSoundsNote':'');
</script>
{#snippet examples()}{#if validation}<div class="examples-column"><ReferencePanel species={validation.species} datasetId={id} validationId={sid} {practice} onexamples={list=>heard=list}/></div>{/if}{/snippet}
<div class="page validation-page"><div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if validation}
    <h1>{commonName(validation.species,app.locale)} <i class="sci">{validation.species.scientific_name}</i></h1>
    <ValidationSteps {validation}/>
    {#if !validation.applicable}<p class="notice">{t(validation.model_reason??(validation.annotation_source_current?'wScopeChanged':'annotation_source_changed'))}</p>{/if}
    {#if validation.stage==='complete'&&!validation.question}<section class="panel"><QualityPanel {validation}/>
      {#if goalMissed(validation)||recallLow(validation)}<CheckAdvice datasetId={id} {validation} searchHref={`${resolve(`/datasets/${id}/search`)}?species=${validation.species.id}&${view}`} modelHref={`${resolve(`/datasets/${id}/species/${validation.species.id}/model`)}?${range}`}/>{/if}
      {#if validation.can_start_recall}<p>{t('vRecallOptional')}</p>{/if}
      <div class="actions"><Button variant="primary" href={back}>{t('wBackSpecies')}</Button>{#if validation.can_start_recall}<Button pending={beginRecall.pending} disabled={busy||!validation.applicable} onclick={()=>beginRecall.run()}>{t('vStartRecall')}</Button>{/if}{#if previous}<Button pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
      {#if beginRecall.error}<p class="error" role="alert">{t(beginRecall.error)}</p>{/if}</section>
    {:else if !started}<section class="panel"><h2>{t('wEvaluate')}</h2><p>{t('wValidationPlan')}</p>
      {#if shared&&validation.counts.calibration.answered}<p>{sharedProgress}</p>{/if}
      <p>{t('vFirstCount').replace('{count}',String(remaining))}</p>
      <p><strong>{t('refListenFirst')}</strong></p>{@render examples()}<Button variant="primary" onclick={()=>started=true}>{t('wValidationStart')}</Button></section>
    {:else}
      <div class="with-examples"><section class="panel">
        {#if phase==='calibration'&&!validation.question}
          <h2>{t('vCalibrationReady')}</h2><p>{t('vCalibrationFreeze')}</p>
          <div class="actions"><Button variant="primary" pending={begin.pending} disabled={busy||!validation.applicable} onclick={()=>begin.run()}>{t('vBeginEvaluation')}</Button>{#if previous}<Button disabled={busy} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
        {:else if paused}
          <h2>{t('wBatchDone')}</h2>
          {#if phase==='evaluation'}<QualityPanel {validation}/>{/if}
          <div class="actions"><Button variant="primary" onclick={()=>paused=false}>{t('wNextBatch')}</Button><Button href={back}>{t('wStopHere')}</Button>{#if previous}<Button disabled={busy} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
        {:else if validation.question}
          {@const question=validation.question}
          <div class="phase-progress"><h2>{t('vQuestionNamed').replace('{name}',commonName(validation.species,app.locale)||validation.species.scientific_name)}</h2>
            <p>{t('vAnsweredCount').replace('{answered}',String(validation.counts[phase].answered)).replace('{total}',String(validation.counts[phase].total)).replace('{remaining}',String(remaining))}{#if corrected}<span role="status"> · ✓ {t('vCorrectionSaved')}</span>{/if}</p></div>
          {#if note}<p class="muted">{t(note)}</p>{/if}
          {#key question.id}<AudioRegion source={question.source} fixedRange zoomable start={question.start_s} end={question.end_s} marks={question.ranges.filter(range=>range.species_id===validation?.species.id)} storageKey={`echoroo-validation:${sid}:${question.id}:view`}/>{/key}
          <div class="actions answers"><Button disabled={busy||!previous} onclick={()=>previous&&navigate.run(previous.id)}>‹ {t('vPreviousSound')}</Button>
            <div class="verdicts" role="group" aria-label={t('wQuestion')}>{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
            <Button size="lg" active={question.verdict===verdict} aria-pressed={question.verdict===verdict} disabled={busy||!validation.applicable} onclick={()=>answer.run(verdict)}>{question.verdict===verdict?'✓ ':''}{t(label)}</Button>{/each}</div>
            <Button disabled={busy||!answered} title={answered?undefined:t('vAnswerToContinue')} onclick={goNext}>{t(nextLabel)} ›</Button>
            <p class="muted unknown-note">{t('vUnknownNote')}</p></div>
          {#if answered&&next}<div class="actions review-navigation"><Button disabled={busy} onclick={()=>navigate.run()}>{t(remaining>0?'vResume':finishLabel)}</Button></div>{/if}
        {/if}
        {#if answer.error||navigate.error||begin.error||beginRecall.error}<p class="error" class:answer-feedback={!!validation.question&&!paused} role="alert">{t(answer.error||navigate.error||begin.error||beginRecall.error)}</p>{/if}
      </section>{@render examples()}</div>
    {/if}
  {:else if !error}<p><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.answer-feedback{position:fixed;bottom:100px;left:28px;right:28px;z-index:4;padding:12px;background:rgb(var(--color-page-bg));box-shadow:0 0 0 1px currentColor;border-radius:8px;}:global(.main:has(.validation-page)){padding-top:16px}.page.validation-page{max-width:1280px;gap:8px;padding-bottom:130px}.with-examples{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,380px);gap:16px;align-items:start}.examples-column{display:flex;flex-direction:column;gap:8px;position:sticky;top:16px}@media(max-width:950px){.with-examples{grid-template-columns:minmax(0,1fr)}.examples-column{position:static}}.validation-page .crumbs{margin-bottom:0}.validation-page .panel{padding:16px}.validation-page h1{font-size:27px;margin:0}.validation-page .sci{display:inline;font-size:16px;margin-left:12px}.phase-progress{display:flex;align-items:center;justify-content:space-between;gap:12px}.validation-page h2{font-size:20px;margin:0 0 8px}p{margin:6px 0}.answers{position:fixed;bottom:0;left:0;right:0;z-index:3;justify-content:space-between;align-items:center;row-gap:4px;padding:10px max(24px,calc((100vw - 1280px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));margin:0}.answers .verdicts{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}.unknown-note{flex-basis:100%;text-align:center;margin:0;font-size:14px}.review-navigation{margin-top:24px}@media(max-width:700px){.answers{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:8px 12px}.answers .verdicts{grid-column:1/-1;order:-1;flex-wrap:nowrap;gap:6px}.answers .verdicts :global(.button){flex:1;padding:8px;font-size:16px;min-height:48px}.unknown-note{grid-column:1/-1}.page.validation-page{padding-bottom:220px}}</style>
