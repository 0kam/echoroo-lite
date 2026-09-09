<script lang="ts">
  export type Band = { low: number; high: number; count: number; target: number; other: number; unknown: number };
  let { bands, t }: { bands: Band[]; t: (key: string) => string } = $props();
  const maximum = $derived(Math.max(1, ...bands.map(band => band.count)));
</script>

<p class="muted">{t('distributionHint')}</p>
<table><thead><tr><th>{t('similarity')}</th><th>{t('eligibleWindows')}</th><th>{t('verdict_target')}</th><th>{t('verdict_other')}</th><th>{t('verdict_unknown')}</th></tr></thead><tbody>
  {#each bands as band, i}<tr><td>{band.low.toFixed(1)}–{band.high.toFixed(1)}{i === bands.length - 1 ? '' : ' *'}</td>
    <td class="count"><span class="bar" style:width={`${band.count / maximum * 100}%`} aria-hidden="true"></span><span class="value">{band.count}</span></td>
    <td>{band.target}</td><td>{band.other}</td><td>{band.unknown}</td></tr>{/each}
</tbody></table>

<style>
  .count { position: relative; min-width: 100px; }
  .bar { position: absolute; left: 0; top: 15%; height: 70%; background: rgb(var(--primary-200)); }
  .value { position: relative; }
</style>
