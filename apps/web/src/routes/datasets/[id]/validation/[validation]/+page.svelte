<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount,untrack } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { validationPhase,type Validation } from '$lib/workflow';
  import RangeEditor from '$lib/RangeEditor.svelte';
  import type { CallRange } from '$lib/annotations';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),sid=Number(page.params.validation);
  let validation=$state<Validation|null>(null),error=$state(''),paused=$state(false),started=$state(false),corrected=$state(false);
  let ranges=$state<CallRange[]>([]),reviewVerdicts=$state<Record<number,string>>({}),storageError=$state(false);
  const draftKey=$derived(validation?.question?`echoroo-validation:${sid}:${validation.question.id}`:'');
  const reviewVersion=(value:Validation['question'])=>value?.reviews?.map(review=>`${review.session_id}:${review.item_id}:${review.version}`).join('|')??'';
  function show(value:Validation){validation=value;ranges=value.question?.ranges??[];reviewVerdicts=Object.fromEntries(value.question?.reviews?.filter(review=>review.verdict).map(review=>[review.session_id,review.verdict!])??[]);
    if(value.question)try{const draft=JSON.parse(localStorage.getItem(`echoroo-validation:${sid}:${value.question.id}`)||'null');if(draft?.version===value.question.version&&draft?.reviewVersion===reviewVersion(value.question)){ranges=draft.ranges;reviewVerdicts=draft.reviewVerdicts??reviewVerdicts;}}catch{storageError=true;}}
  $effect(()=>{const question=validation?.question,key=draftKey,entries=$state.snapshot(ranges),reviews=$state.snapshot(reviewVerdicts);if(!question||!key)return;
    untrack(()=>{try{localStorage.setItem(key,JSON.stringify({version:question.version,reviewVersion:reviewVersion(question),ranges:entries,reviewVerdicts:reviews}));storageError=false;}catch{storageError=true;}});});
  const phase=$derived(validationPhase(validation));
  const previous=$derived(validation?.history.filter(item=>item.position<(validation?.question?.position??Infinity)).at(-1));
  const next=$derived(validation?.history.find(item=>item.position>(validation?.question?.position??Infinity)));
  // An answered sound always has a next sound: a later answered one, or the first unanswered one (or the finished step).
  const goNext=()=>navigate.run(next?.id);
  const answered=$derived(!!validation?.question?.verdict);
  const remaining=$derived(validation?validation.counts[phase].total-validation.counts[phase].answered:0);
  const finishLabel=$derived(phase==='calibration'?'vBackCalibrationReady':'wValidationDone');
  const nextLabel=$derived(next||remaining>0?'vNextSound':finishLabel);
  type Step='calibration'|'evaluation'|'recall';
  const steps=$derived(validation?[...(validation.counts.calibration.total?['calibration' as Step]:[]),'evaluation' as Step,...(validation.method==='fixed_precision_audit_v1'?['recall' as Step]:[])]:[]);
  function stepState(value:Validation,step:Step){
    const precisionDone=value.stage==='complete'||value.audit_step==='recall';
    if(step==='calibration')return value.stage==='calibration'?'current':'done';
    if(step==='evaluation')return value.stage==='calibration'?'todo':precisionDone?'done':'current';
    const recall=value.counts.recall;
    return value.audit_step!=='recall'?'todo':recall?.total&&recall.answered===recall.total?'done':'current';
  }
  onMount(()=>{api<Validation>(`/validations/${sid}`).then(value=>{show(value);started=value.stage!=='calibration'||value.counts.calibration.answered>0;const active=validationPhase(value);
    paused=active!=='calibration'&&value.counts[active].answered>0&&value.counts[active].answered%10===0;
  }).catch(e=>error=e.message);});
  const navigate=createTask(async(itemId?:number)=>{show(await api<Validation>(`/validations/${sid}${itemId?`?item_id=${itemId}`:''}`));paused=false;started=true;corrected=false;});
  const begin=createTask(async()=>{if(!validation?.applicable)return;show(await api<Validation>(`/validations/${sid}/evaluation`,{}));paused=false;corrected=false;});
  const beginRecall=createTask(async()=>{if(!validation?.applicable)return;show(await api<Validation>(`/validations/${sid}/recall`,{}));paused=false;started=true;corrected=false;});
  const answer=createTask(async(verdict?:string)=>{if(!validation?.applicable||!validation.question)return;const question=validation.question,reviews=question.reviews;
    const correcting=reviews?.length?reviews.every(review=>review.verdict!==null):question.verdict!==null;
    const payload=reviews?.length?{item_id:question.id,verdict:reviewVerdicts[reviews[0].session_id],ranges,version:question.version,
      reviews:reviews.map(review=>({session_id:review.session_id,item_id:review.item_id,version:review.version,verdict:reviewVerdicts[review.session_id]}))}:{item_id:question.id,verdict,ranges,version:question.version};
    const value=await api<Validation>(`/validations/${sid}/answers`,payload);
    try{localStorage.removeItem(draftKey);}catch{storageError=true;}
    // Correcting an earlier sound moves on to the following sound, just like a first answer.
    corrected=correcting;
    if(correcting){show(next?await api<Validation>(`/validations/${sid}?item_id=${next.id}`):value);return;}
    show(value);
    if(value.counts[phase].answered>0&&value.counts[phase].answered%10===0)paused=true;
  });
  const busy=$derived(answer.pending||navigate.pending||begin.pending||beginRecall.pending);
  const back=$derived(validation?`${resolve(`/datasets/${id}/species/${validation.species.id}`)}?start_date=${validation.start_date}&end_date=${validation.end_date}${validation.run_id?`&run_id=${validation.run_id}`:''}${validation.model_id?`&model_id=${validation.model_id}&engine=custom`:''}`:resolve(`/datasets/${id}`));
  const practice=$derived(validation?`${resolve('/quiz')}?species=${validation.species.id}&return=${encodeURIComponent(`/datasets/${id}/validation/${sid}`)}`:'');
  const shared=$derived(validation?.annotation_session_id?`${resolve(`/datasets/${id}/annotations`)}?start_date=${validation.start_date}&end_date=${validation.end_date}&view=list`:'');
  const groupReady=$derived(validation?.question?.reviews?.every(review=>!!reviewVerdicts[review.session_id])??false);
</script>
<div class="page validation-page"><div class="crumbs"><a href={shared||back}>{t(shared?'aReviewShared':'wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if validation}
    <h1>{validation.members?.length?t('vGroupTitle'):commonName(validation.species,app.locale)}{#if !validation.members?.length} <i class="sci">{validation.species.scientific_name}</i>{/if}</h1>
    <ol class="phase-steps" aria-label={t('vSteps')}>{#each steps as step,index}{@const state=stepState(validation,step)}
      <li class={state} aria-current={state==='current'?'step':undefined}><span class="step-number" aria-hidden="true">{state==='done'?'✓':index+1}</span>{t(step==='calibration'?'vStepAdjust':step==='evaluation'?'vStepPrecision':'vStepRecall')}{#if state==='done'}<span class="visually-hidden"> · {t('vStepDone')}</span>{/if}</li>{/each}</ol>
    {#if validation.review_species?.length&&!validation.question}<p>{validation.review_species.map(species=>commonName(species,app.locale)||species.scientific_name).join('・')}</p>{/if}
    {#if !started||!validation.question}<p>{validation.start_date}–{validation.end_date}</p>{/if}
    {#if !validation.applicable}<p class="notice">{t(validation.model_reason??(validation.annotation_source_current?'wScopeChanged':'annotation_source_changed'))}</p>{/if}
    {#if validation.stage==='complete'&&!validation.question}<section class="panel">{#each validation.members??[validation] as member}<div class="member-result">{#if validation.members?.length}<h2>{commonName(member.species,app.locale)||member.species.scientific_name}</h2>{/if}<QualityPanel validation={member}/></div>{/each}
      {#if validation.can_start_recall}<p>{t('vRecallOptional')}</p>{/if}
      <div class="actions"><Button href={shared||back}>{t(shared?'aReviewShared':'wBackSpecies')}</Button>{#if validation.can_start_recall}<Button pending={beginRecall.pending} disabled={busy||!validation.applicable} onclick={()=>beginRecall.run()}>{t('vStartRecall')}</Button>{/if}{#if previous}<Button pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
      {#if beginRecall.error}<p class="error" role="alert">{t(beginRecall.error)}</p>{/if}</section>
    {:else if !started}<section class="panel"><h2>{t('wEvaluate')}</h2><p>{shared?t(validation.members?.length?'aSharedProgressGroup':'aSharedProgress').replace('{answered}',String(validation.counts.calibration.answered)).replace('{remaining}',String(validation.counts.calibration.total-validation.counts.calibration.answered)):t(validation.kind==='search'?'rSearchValidationPlan':'wValidationPlan')}</p>
      <p>{t('uReviewCount')}: {validation.counts.calibration.total}</p><p>{t('uSavedAlways')}</p><Button variant="primary" onclick={()=>started=true}>{t('wValidationStart')}</Button></section>
    {:else}
      <section class="panel"><div class="phase-progress">{#if corrected}<p role="status">✓ {t('vCorrectionSaved')}</p>{/if}<p>{t('vAnsweredCount').replace('{answered}',String(validation.counts[phase].answered)).replace('{total}',String(validation.counts[phase].total)).replace('{remaining}',String(validation.counts[phase].total-validation.counts[phase].answered))}</p></div>
        {#if validation.method==='fixed_precision_audit_v1'&&phase==='evaluation'}<p class="muted">{t('vFixedEvaluationPace').replace('{total}',String(validation.counts.evaluation.total))}</p>
        {:else if phase==='evaluation'}<p class="muted">{t('vEvaluationPace').replace('{total}',String(validation.counts.evaluation.total))}</p>{/if}
        {#if shared&&!(validation.members?.length&&validation.question)}<p class="muted">{phase==='calibration'?t(validation.members?.length?'aSharedProgressGroup':'aSharedProgress').replace('{answered}',String(validation.counts.calibration.answered)).replace('{remaining}',String(validation.counts.calibration.total-validation.counts.calibration.answered)):t('aHoldout')}</p>{/if}
        {#if phase==='calibration'&&!validation.question}
          <p>{t('vCalibrationReady')}</p><p class="muted">{t('vCalibrationFreeze')}</p>
          <Button variant="primary" pending={begin.pending} disabled={busy||!validation.applicable} onclick={()=>begin.run()}>{t('vBeginEvaluation')}</Button>
        {:else if paused}
          {#if phase==='evaluation'}{#each validation.members??[validation] as member}<div class="member-result">{#if validation.members?.length}<h2>{commonName(member.species,app.locale)||member.species.scientific_name}</h2>{/if}<QualityPanel validation={member}/></div>{/each}{:else}<p>{t('wBatchDone')}</p>{/if}
          <Button variant="primary" onclick={()=>paused=false}>{t('wNextBatch')}</Button>
        {:else if validation.question}
          {#key validation.question.id}<RangeEditor source={validation.question.source} start={validation.question.start_s} end={validation.question.end_s}
            palette={validation.members?.length?validation.review_species??validation.palette:validation.palette} bind:ranges target={validation.members?.length?undefined:validation.species} storageKey={draftKey} disabled={busy||!validation.applicable}/>{/key}
          {#if storageError}<p class="error">{t('aStorageFailed')}</p>{/if}
          {#if validation.question.reviews?.length&&validation.question.verdict}<p role="status">{t('vGroupAnswersSaved')}</p>{/if}
          {#if validation.question.reviews?.length}<div class="group-answers"><div class="review-grid">{#each validation.question.reviews as review}<fieldset><legend>{commonName(review.species,app.locale)||review.species.scientific_name}</legend><div class="actions">{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}<Button active={reviewVerdicts[review.session_id]===verdict} aria-pressed={reviewVerdicts[review.session_id]===verdict} disabled={busy||!validation.applicable} onclick={()=>reviewVerdicts={...reviewVerdicts,[review.session_id]:verdict}}>{reviewVerdicts[review.session_id]===verdict?'✓ ':''}{t(label)}</Button>{/each}</div></fieldset>{/each}</div><div class="actions group-navigation">{#if previous}<Button disabled={busy} pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vPreviousSound')}</Button>{/if}<Button variant="primary" pending={answer.pending} disabled={busy||!groupReady||!validation.applicable} onclick={()=>answer.run()}>{t('vSaveGroupAnswers')}</Button>{#if answered}<Button disabled={busy} onclick={goNext}>{t(nextLabel)} ›</Button>{/if}</div></div>
          {:else}<div class="actions answers"><Button disabled={busy||!previous} onclick={()=>previous&&navigate.run(previous.id)}>‹ {t('vPreviousSound')}</Button>
            <div class="verdicts" role="group" aria-label={t('wQuestion')}>{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
            <Button size="lg" active={validation.question.verdict===verdict} aria-pressed={validation.question.verdict===verdict} disabled={busy||!validation.applicable} onclick={()=>answer.run(verdict)}>{validation.question.verdict===verdict?'✓ ':''}{t(label)}</Button>{/each}</div>
            <Button disabled={busy||!answered} title={answered?undefined:t('vAnswerToContinue')} onclick={goNext}>{t(nextLabel)} ›</Button></div>{/if}
        {/if}
        <div class="actions review-navigation">
          {#if previous&&(!validation.question||paused)}<Button disabled={busy} pending={navigate.pending} onclick={()=>navigate.run(previous.id)}>{t('vPreviousSound')}</Button>{/if}
          {#if answered&&next}<Button disabled={busy} onclick={()=>navigate.run()}>{t(remaining>0?'vResume':finishLabel)}</Button>{/if}
        </div>
        {#if answer.error||navigate.error||begin.error||beginRecall.error}<p class="error" class:answer-feedback={!!validation.question&&!paused} role="alert">{t(answer.error||navigate.error||begin.error||beginRecall.error)}</p>{/if}
      </section>
      <div class="actions">{#if validation.members?.length}<Button href={shared||back}>{t(shared?'aReviewShared':'uBackResults')}</Button>{:else}<Button href={back}>{t('wPause')}</Button><Button href={practice}>{t('wPractice')}</Button>{/if}</div>
    {/if}
  {:else if !error}<p><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.answer-feedback{position:fixed;bottom:76px;left:28px;right:28px;z-index:4;padding:12px;background:rgb(var(--color-page-bg));box-shadow:0 0 0 1px currentColor;border-radius:8px;}:global(.main:has(.validation-page)){padding-top:16px}.page.validation-page{max-width:1100px;gap:8px;padding-bottom:100px}.validation-page .crumbs{margin-bottom:0}.validation-page .panel{padding:16px}.validation-page h1{font-size:27px;margin:0}.validation-page .sci{display:inline;font-size:16px;margin-left:12px}.phase-progress{display:flex;align-items:center;justify-content:space-between;gap:12px}.validation-page h2{font-size:20px;margin-bottom:8px}p{margin:6px 0}.phase-steps{display:flex;flex-wrap:wrap;gap:6px 20px;margin:4px 0 8px;padding:0;list-style:none;font-size:15px;color:rgb(var(--stone-700))}.phase-steps li{display:flex;align-items:center;gap:8px}.phase-steps li.current{font-weight:700;color:rgb(var(--primary-700))}.step-number{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;border:2px solid currentColor;font-size:13px;font-weight:700}:global(.dark) .phase-steps li.current{color:rgb(var(--primary-500))}.phase-steps li.current .step-number{border-width:3px}.visually-hidden{position:absolute;width:1px;height:1px;overflow:hidden;clip-path:inset(50%);white-space:nowrap}.answers .verdicts{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}.answers{position:fixed;bottom:0;left:0;right:0;z-index:3;justify-content:space-between;align-items:center;padding:12px max(24px,calc((100vw - 1100px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));margin:0}.review-navigation{margin-top:24px}.group-answers{position:fixed;z-index:3;left:0;right:0;bottom:0;display:flex;align-items:center;gap:12px;padding:8px max(24px,calc((100vw - 1180px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));box-shadow:0 -4px 12px rgb(var(--stone-900)/.08)}.review-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;flex:1;max-height:32vh;overflow:auto}.group-answers fieldset{min-width:0;padding:4px 8px 8px;border:1px solid rgb(var(--stone-300));border-radius:8px}.group-answers legend{max-width:100%;padding:0 4px;font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.group-answers fieldset .actions{gap:6px;flex-wrap:nowrap}.group-navigation{flex:0 0 auto;flex-direction:column}.member-result+.member-result{border-top:1px solid rgb(var(--stone-300));margin-top:20px;padding-top:20px}@media(max-width:700px){.answers{display:grid;grid-template-columns:1fr 1fr;gap:6px;padding:8px 12px}.answers .verdicts{grid-column:1/-1;order:-1;flex-wrap:nowrap;gap:6px}.answers .verdicts :global(.button){flex:1;padding:8px;font-size:16px;min-height:48px}}@media(max-width:850px){.group-answers{align-items:stretch;flex-direction:column}.review-grid{grid-template-columns:1fr;width:100%}.group-navigation{flex-direction:row}.page.validation-page{padding-bottom:220px}}</style>
