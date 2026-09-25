<script lang="ts">
  import { t } from './app.svelte';
  import { percent } from './reports';
  import { qualityLines,qualityText,modelName,interval,type Validation } from './workflow';
  let {validation}:{validation:Validation|null}=$props();
</script>
<h2>{t('wQuality')}</h2>{#each qualityLines(validation) as line}<p><strong>{line}</strong></p>{/each}
{#if validation&&!validation.applicable}<p class="notice">{t(validation.model_reason??'wScopeChanged')}</p>{/if}
{#if validation&&Object.keys(validation.results).length}
  {#if validation.method!=='fixed_precision_audit_v1'&&validation.stage!=='complete'}<p>{t('vInterimCount').replace('{count}',String(validation.evaluated_count))}</p><p class="muted">{t('vInterimHint')}</p>{/if}
  <details><summary>{t(validation.applicable?'uMoreQuality':'wPreviousQuality')}</summary>
  {#each Object.entries(validation.results) as [engine,result]}
    <section class="quality-model"><h3>{modelName(engine)}</h3>
      {#if !validation.applicable}<p>{qualityText({...validation,applicable:true},result)}</p>{/if}
      {#if result.precision_sample}<p class="muted">{t('vModelPrecisionSample').replace('{answered}',String(result.precision_sample.answered)).replace('{total}',String(result.precision_sample.total)).replace('{population}',String(result.precision_sample.population))}</p>{/if}
      {#if validation.method!=='fixed_precision_audit_v1'||result.precision_complete}{#each [['wPrecision',result.precision],['wRecall',result.recall],['wRate',result.estimated_window_rate]] as [label,metric]}
        {@const value=metric as typeof result.precision}
        <div class="metric"><strong>{t(label as string)}</strong><span>{percent(value.value)}</span><small>{t('wInterval')}: {interval(value)}</small>
          {#if label==='wPrecision'&&result.precision_complete}<small>{t('vOneSidedLower')}: {percent(result.precision_lower_95??null)}</small>{/if}
          {#if value.value===null}<small>{t(label==='wRecall'&&result.precision_complete&&!result.recall_complete?'vRecallNotChecked':'wInsufficient')}</small>{/if}</div>
      {/each}{/if}
      <p class="muted">{t('wUnknown')}: {result.unknown} / {result.reviewed} · {t('wThreshold')}: {result.threshold?.toFixed(2)??'—'}</p>
    </section>
  {/each}
  <p class="muted">{validation.start_date}–{validation.end_date} · {validation.window_s} {t('seconds')} · {t('rQualityScope')}</p>
  </details>
{/if}
{#if validation?.applicable&&validation.stage==='evaluation'&&Object.values(validation.thresholds).some(value=>value.adjusted)}<p>{t('rThresholdApplied')}</p>{/if}
<style>.quality-model{padding:12px 0;border-top:1px solid rgb(var(--stone-300))}.metric{display:grid;grid-template-columns:1fr auto;gap:4px 10px;margin:12px 0}.metric small{grid-column:1/-1;color:rgb(var(--stone-700))}p{margin:10px 0}</style>
