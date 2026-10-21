<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { EXTRA_SOUNDS,fill,modelName,rangeQuery,workload,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import QualityPanel from '$lib/QualityPanel.svelte';
  import ReferencePanel,{type ExampleRef} from '$lib/ReferencePanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),sid=Number(page.params.validation);
  let validation=$state<Validation|null>(null),error=$state(''),started=$state(false),corrected=$state(false),heard=$state<ExampleRef[]>();
  // The answer bar is fixed to the bottom; the page keeps that much space free so it never covers the spectrogram.
  let barHeight=$state(0);
  const previous=$derived(validation?.history.filter(item=>item.position<(validation?.question?.position??Infinity)).at(-1));
  const next=$derived(validation?.history.find(item=>item.position>(validation?.question?.position??Infinity)));
  // An answered sound always has a next sound: a later answered one, or the first unanswered one (or the result).
  const goNext=()=>navigate.run(next?.id);
  const answered=$derived(!!validation?.question?.verdict);
  const remaining=$derived(validation?validation.counts.total-validation.counts.answered:0);
  const nextLabel=$derived(next||remaining>0?'vNextSound':'wValidationDone');
  onMount(()=>{api<Validation>(`/validations/${sid}`).then(value=>{validation=value;started=value.counts.answered>0;}).catch(e=>error=e.message);});
  const navigate=createTask(async(itemId?:number)=>{validation=await api<Validation>(`/validations/${sid}${itemId?`?item_id=${itemId}`:''}`);started=true;corrected=false;});
  const extend=createTask(async()=>{if(!validation?.applicable)return;validation=await api<Validation>(`/validations/${sid}/extend`,{});corrected=false;});
  const answer=createTask(async(verdict:string)=>{if(!validation?.applicable||!validation.question)return;const question=validation.question;
    const value=await api<Validation>(`/validations/${sid}/answers`,{item_id:question.id,verdict,version:question.version,examples_snapshot:heard});
    // Correcting an earlier sound moves on to the following sound, just like a first answer.
    corrected=question.verdict!==null;
    validation=corrected&&next?await api<Validation>(`/validations/${sid}?item_id=${next.id}`):value;
  });
  const busy=$derived(answer.pending||navigate.pending||extend.pending);
  const done=$derived(validation?.stage==='complete'&&!validation.question);
  // The species results tab this check belongs to, so pages opened from here return to it.
  const range=$derived(validation?rangeQuery(validation.start_date,validation.end_date):'');
  const view=$derived(validation?`${range}&engine=${validation.engine}${validation.model_id?`&model_id=${validation.model_id}`:''}`:'');
  const back=$derived(validation?`${resolve(`/datasets/${id}/species/${validation.species.id}`)}?${view}`:resolve(`/datasets/${id}`));
  const practice=$derived(validation?`${resolve('/quiz')}?species=${validation.species.id}&return=${encodeURIComponent(`/datasets/${id}/validation/${sid}`)}`:'');
</script>
{#snippet examples()}{#if validation}<div class="examples-column"><ReferencePanel species={validation.species} datasetId={id} validationId={sid} {practice} onexamples={list=>heard=list}/></div>{/if}{/snippet}
<div class="page wide validation-page" style:padding-bottom={started&&!done&&validation?.question?`${barHeight+24}px`:undefined}><div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if validation}
    {#if done||!started}<h1>{commonName(validation.species,app.locale)} <i class="sci">{validation.species.scientific_name}</i></h1>
      <p class="model">{fill('vModelTitle',{model:modelName(validation.engine)})} · {fill('wEvery',{n:validation.window_s})}</p>
    {:else if validation.question}{@const question=validation.question}
      <!-- One heading: the question. The model is the subheading. -->
      <div class="phase-progress"><div><h1>{fill('vQuestionNamed',{name:commonName(validation.species,app.locale)||validation.species.scientific_name})}</h1>
        <h2 class="model">{fill('vModelTitle',{model:modelName(validation.engine)})} · {fill('wEvery',{n:validation.window_s})}</h2></div>
        <p>{#if answered}{fill('vReviewProgress',{n:question.review_position,total:question.review_total})}{:else}{fill('vProgress',{answered:validation.counts.answered,total:validation.counts.total})}{/if}{#if corrected}<span role="status"> · ✓ {t('vCorrectionSaved')}</span>{/if}</p></div>
    {/if}
    {#if !validation.applicable}<p class="notice">{t(validation.model_reason??'wScopeChanged')}</p>{/if}
    {#if done}<section class="panel"><h2>{t('vResultTitle')}</h2><QualityPanel {validation}/>
      {#if validation.can_extend}<p><strong>{t('vExtendWhy')}</strong></p>{/if}
      <div class="actions">{#if validation.can_extend}<Button variant="primary" href={back}>{t('vFinishHere')}</Button><Button pending={extend.pending} disabled={busy||!validation.applicable} onclick={()=>extend.run()}>{t('vExtend')}</Button>
        {:else}<Button variant="primary" href={back}>{t('wBackSpecies')}</Button>{/if}
        {#if previous}<Button disabled={busy} onclick={()=>navigate.run(previous.id)}>{t('vReviewAnswers')}</Button>{/if}</div>
      {#if extend.error||navigate.error}<p class="error" role="alert">{t(extend.error||navigate.error)}</p>{/if}</section>
    {:else if !started}{@const extra=validation.population>validation.counts.total?EXTRA_SOUNDS:0}{@const effort=workload(validation.counts.total,extra)}<div class="with-examples"><section class="panel stack"><h2>{t('cStart')}</h2><p>{fill('wValidationPlan',{total:validation.counts.total,more:extra?t('vMore18'):'',low:effort.low,high:effort.high})}</p>
      <p><strong>{t('refListenFirst')}</strong></p><Button variant="primary" onclick={()=>started=true}>{t('wValidationStart')}</Button></section>{@render examples()}</div>
    {:else}
      <div class="with-examples"><section class="panel">
        {#if validation.question}
          {@const question=validation.question}
          <p class="muted">{t('vMixedNote')}</p>
          {#key question.id}<AudioRegion source={question.source} fixedRange zoomable start={question.start_s} end={question.end_s} storageKey={`echoroo-validation:${sid}:${question.id}:view`}/>{/key}
          <div class="actions answers" bind:offsetHeight={barHeight}><Button disabled={busy||!previous} onclick={()=>previous&&navigate.run(previous.id)}>‹ {t('vPreviousSound')}</Button>
            <div class="verdicts" role="group" aria-label={t('wQuestion')}>{#each [['target','wHeard'],['other','wNotHeard'],['unknown','wCannotTell']] as [verdict,label]}
            <Button size="lg" active={question.verdict===verdict} aria-pressed={question.verdict===verdict} disabled={busy||!validation.applicable} onclick={()=>answer.run(verdict)}>{question.verdict===verdict?'✓ ':''}{t(label)}</Button>{/each}</div>
            <Button disabled={busy||!answered} title={answered?undefined:t('vAnswerToContinue')} onclick={goNext}>{t(nextLabel)} ›</Button>
            <p class="muted unknown-note">{t('vUnknownNote')}</p></div>
          {#if answered&&next}<div class="actions review-navigation"><Button disabled={busy} onclick={()=>navigate.run()}>{t(remaining>0?'vResume':'wValidationDone')}</Button></div>{/if}
        {/if}
        {#if answer.error||navigate.error}<p class="error" class:answer-feedback={!!validation.question} style:bottom={validation.question?`${barHeight+8}px`:undefined} role="alert">{t(answer.error||navigate.error)}</p>{/if}
      </section>{@render examples()}</div>
    {/if}
  {:else if !error}<p role="status"><Spinner/> {t('vPreparing')}</p>{/if}
</div>
<style>.answer-feedback{position:fixed;left:24px;right:24px;z-index:4;padding:12px;background:rgb(var(--color-page-bg));box-shadow:0 0 0 1px currentColor;border-radius:8px}
  :global(.main:has(.validation-page)){padding-top:16px}.page.validation-page{gap:8px}
  .with-examples{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,380px);gap:16px;align-items:start}.examples-column{display:flex;flex-direction:column;gap:8px;position:sticky;top:16px}
  @media(max-width:950px){.with-examples{grid-template-columns:minmax(0,1fr)}.examples-column{position:static}}
  .validation-page .crumbs{margin-bottom:0}.validation-page .panel{padding:16px}.validation-page h1{margin:0;line-height:1.25}h2.model,p.model{font-size:16px;font-weight:600;margin:0}
  .validation-page .sci{display:inline;font-size:16px;margin-left:12px;white-space:nowrap}
  .phase-progress{display:flex;align-items:flex-end;justify-content:space-between;flex-wrap:wrap;gap:4px 12px}.phase-progress p{white-space:nowrap}
  .validation-page section h2{font-size:21px;margin:0 0 8px}p{margin:4px 0}
  .answers{position:fixed;bottom:0;left:0;right:0;z-index:3;justify-content:space-between;align-items:center;row-gap:4px;padding:12px max(24px,calc((100vw - 1280px)/2));background:rgb(var(--color-page-bg));border-top:1px solid rgb(var(--stone-300));margin:0}
  .answers .verdicts{display:flex;flex-wrap:wrap;justify-content:center;gap:8px}.unknown-note{flex-basis:100%;text-align:center;margin:0;font-size:14px}.review-navigation{margin-top:24px}
  @media(max-width:700px){.answers{display:grid;grid-template-columns:1fr 1fr;gap:4px 8px;padding:8px 12px}.answers .verdicts{grid-column:1/-1;order:-1;flex-wrap:nowrap;gap:4px}.answers .verdicts :global(.button){flex:1;padding:8px;font-size:16px;min-height:48px}.unknown-note{grid-column:1/-1}}</style>
