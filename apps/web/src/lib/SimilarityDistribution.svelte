<script lang="ts">
  import { t } from './app.svelte';
  export type Band = { low: number; high: number; count: number; target: number; other: number; unknown: number };
  let { bands, hint }: { bands: Band[]; hint: string } = $props();
  const maximum = $derived(Math.max(1, ...bands.map(band => band.count)));
</script>

<p class="muted">{hint}</p>
<div class="table-wrap">
<table><thead><tr><th>{t('similarity')}</th><th>{t('eligibleWindows')}</th><th>{t('verdict_target')}</th><th>{t('verdict_other')}</th><th>{t('verdict_unknown')}</th></tr></thead><tbody>
  {#each bands as band, i}<tr><td class="mono">{band.low.toFixed(1)}–{band.high.toFixed(1)}{i === bands.length - 1 ? '' : ' *'}</td>
    <td class="count mono"><span class="bar" style:width={`${band.count / maximum * 100}%`} aria-hidden="true"></span><span class="value">{band.count}</span></td>
    <td class="mono">{band.target}</td><td class="mono">{band.other}</td><td class="mono">{band.unknown}</td></tr>{/each}
</tbody></table>
</div>

<style>
  .count { position: relative; min-width: 120px; }
  .bar { position: absolute; left: 0; top: 15%; height: 70%; background: rgb(var(--primary-200)); }
  .value { position: relative; }
</style>
