<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from './app.svelte';
  import { canRebuild,goalMissed,type Validation } from './workflow';
  import Button from './ui/Button.svelte';
  /** Next steps after a finished check falls short: too many wrong detections, or (advice only) too many missed calls. */
  let { datasetId,validation,searchHref,modelHref }:{datasetId:number;validation:Validation;searchHref:string;modelHref:string}=$props();
  let rebuild=$state(false);
  onMount(()=>void canRebuild(datasetId,validation.species.id,validation.start_date,validation.end_date).then(value=>rebuild=value));
  const missed=$derived(goalMissed(validation)),collected=$derived(validation.kind==='search');
  const title=$derived(missed?(collected?'vSearchMissedTitle':'vGoalMissedTitle'):(collected?'vSearchRecallLowTitle':'vRecallLowTitle'));
</script>
<div class="next-step"><p><strong>{t(title)}</strong> {t(missed?'vGoalMissedHint':'vRecallLowHint')}</p><p>{t(missed?'vGoalMissedNext':'vRecallLowNext')}</p>
  <div class="actions"><Button href={searchHref}>{t(collected?'sCollectAgain':'sCheckYourself')}</Button>{#if rebuild}<Button href={modelHref}>{t('customModelTitle')}</Button>{/if}</div></div>
<style>.next-step{margin:16px 0 12px;padding:12px 14px;border:1px solid rgb(var(--color-warning));border-radius:8px;background:rgb(var(--color-warning-light))}.next-step p{margin:0 0 10px}</style>
