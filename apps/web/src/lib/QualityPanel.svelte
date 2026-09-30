<script lang="ts">
  import { t } from './app.svelte';
  import { percent } from './reports';
  import { checkText,precisionText,recallText,interval,type Validation } from './workflow';
  /** One model's check: its status line, then the numbers behind it. */
  let {validation}:{validation:Validation|null}=$props();
  const result=$derived(validation?.results[validation.engine]);
</script>
<p><strong>{checkText(validation)}</strong>{#if precisionText(validation)}<br>{precisionText(validation)}{/if}{#if recallText(validation)}<br>{recallText(validation)}{/if}</p>
{#if validation&&!validation.applicable}<p class="notice">{t(validation.model_reason??'wScopeChanged')}</p>{/if}
{#if validation?.status==='undecided'}<p class="muted">{t('vUndecidedHint')}</p>{/if}
{#if validation&&result?.precision_sample}
  <details><summary>{t(validation.applicable?'uMoreQuality':'wPreviousQuality')}</summary>
    <p class="muted">{t('vModelPrecisionSample').replace('{answered}',String(result.precision_sample.answered)).replace('{total}',String(result.precision_sample.total)).replace('{population}',String(result.precision_sample.population))}</p>
    {#if result.precision_complete}{#each [['wPrecision',result.precision],['wRecall',result.recall],['wRate',result.estimated_window_rate]] as [label,metric]}
      {@const value=metric as NonNullable<typeof result.precision>}
      <div class="metric"><strong>{t(label as string)}</strong><span>{percent(value.value)}</span><small>{t('wInterval')}: {interval(value)}</small>
        {#if label==='wPrecision'}<small>{t('vOneSidedLower')}: {percent(result.precision_lower_95??null)}</small>{/if}
        {#if value.value===null}<small>{t(label==='wRecall'&&!result.recall_complete?'vRecallNotChecked':'wInsufficient')}</small>{/if}</div>
    {/each}{/if}
    <p class="muted">{t('wUnknown')}: {result.unknown} / {result.reviewed} · {t('wThreshold')}: {result.threshold?.toPrecision(3)??'—'}</p>
    <p class="muted">{validation.start_date}–{validation.end_date} · {validation.window_s} {t('seconds')} · {t('rQualityScope')}</p>
  </details>
{/if}
<style>.metric{display:grid;grid-template-columns:1fr auto;gap:4px 10px;margin:12px 0}.metric small{grid-column:1/-1;color:rgb(var(--stone-700))}p{margin:10px 0}</style>
