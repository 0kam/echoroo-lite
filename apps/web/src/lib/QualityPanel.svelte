<script lang="ts">
  import { t } from './app.svelte';
  import { percent } from './reports';
  import { qualityStatus,interval,type Validation } from './workflow';
  let {validation}:{validation:Validation|null}=$props();
</script>
<h2>{t('wQuality')}</h2><p><strong>{t(qualityStatus(validation))}</strong></p>
{#if validation&&!validation.applicable}<p class="notice">{t('wScopeChanged')}</p>{/if}
{#if validation?.stage==='complete'}
  {#each Object.entries(validation.results) as [engine,result]}
    <section class="quality-model"><h3>{engine==='birdnet'?'BirdNET':'Perch'}</h3>
      <p>{t(result.goal_met?'wGoalMet':'wGoalNotMet')}</p>
      {#each [['wPrecision',result.precision],['wRecall',result.recall],['wRate',result.estimated_window_rate]] as [label,metric]}
        {@const value=metric as typeof result.precision}
        <div class="metric"><strong>{t(label as string)}</strong><span>{percent(value.value)}</span><small>{t('wInterval')}: {interval(value)}</small>
          {#if value.value===null}<small>{t('wInsufficient')}</small>{/if}</div>
      {/each}
      <p class="muted">{t('wUnknown')}: {result.unknown} / {result.reviewed} · {t('wThreshold')}: {result.threshold.toFixed(2)}</p>
    </section>
  {/each}
  <p class="muted">{validation.start_date}–{validation.end_date} · {t('wQualityScope')}</p>
{:else}<p class="muted">{t('wQualityHint')}</p>{/if}
<style>.quality-model{padding:12px 0;border-top:1px solid rgb(var(--stone-300))}.metric{display:grid;grid-template-columns:1fr auto;gap:4px 10px;margin:12px 0}.metric small{grid-column:1/-1;color:rgb(var(--stone-700))}p{margin:10px 0}</style>
