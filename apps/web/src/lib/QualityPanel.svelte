<script lang="ts">
  import { app,t,day } from './app.svelte';
  import { checkText,fill,type Metric,type Validation } from './workflow';
  /** One model's check in plain words. Numbers appear only after every sound is answered. */
  // `status` false when the page already states the check's status nearby.
  let {validation,status=true}:{validation:Validation|null;status?:boolean}=$props();
  const result=$derived(validation?.applicable&&validation.stage==='complete'?validation.results[validation.engine]:undefined);
  const share=(value:number|null)=>value==null?'—':new Intl.NumberFormat(app.locale,{style:'percent',maximumSignificantDigits:2}).format(value);
  const about=(value:number|null)=>value==null?'—':new Intl.NumberFormat(app.locale,{maximumSignificantDigits:2}).format(value);
  const shares=(metric:Metric)=>({p:share(metric.value),low:share(metric.low),high:share(metric.high)});
  const sum=(key:'heard'|'not_heard'|'unknown')=>result?.answers_per_bin.reduce((total,bin)=>total+bin[key],0)??0;
</script>
{#if status}<p><strong>{checkText(validation)}</strong></p>{/if}
{#if validation&&!validation.applicable}<p class="notice">{t(validation.model_reason??'wScopeChanged')}</p>{/if}
{#if validation&&result}
  {#if result.status==='undecided'}<p>{fill('rUndecided',{reason:t(`reason_${result.reason}`)})}</p>{/if}
  <ul>
    <li>{fill('rDensity',{...shares(result.density),n:about(Math.round(result.count.value??0)),nlow:about(Math.round(result.count.low??0)),nhigh:about(Math.round(result.count.high??0))})}</li>
    {#if result.precision}<li>{fill('rPrecision',shares(result.precision))}</li>{/if}
    {#if result.recall_status==='not_estimable_rare'}<li>{t('rRecallRare')}</li>{:else if result.recall}<li>{fill('rRecall',shares(result.recall))}</li>{/if}
    {#if result.threshold!=null}<li>{fill('rThreshold',{threshold:+result.threshold.toPrecision(2),window:validation.window_s})}</li>{/if}
    {#if result.unknown}<li>{fill('rUnknown',{n:result.unknown})}</li>{/if}
  </ul>
  {#if result.disagreement}<p class="notice">{t('rDisagreement')}</p>{/if}
  <details><summary>{t('uMoreQuality')}</summary>
    <p>{fill('rAnswers',{total:validation.counts.answered,heard:sum('heard'),not_heard:sum('not_heard'),unknown:sum('unknown')})}</p>
    <p class="muted">{fill('rScope',{first:day(validation.start_date),last:day(validation.end_date),population:validation.population.toLocaleString(app.locale),window:validation.window_s})}</p>
  </details>
{/if}
<style>p{margin:10px 0}ul{margin:10px 0;padding-left:1.2em;display:grid;gap:6px}</style>
