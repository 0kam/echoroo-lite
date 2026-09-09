<script lang="ts">
  import { t } from './app.svelte';
  export type Band = { low: number; high: number; count: number; target: number; other: number; unknown: number };
  let { bands }: { bands: Band[]; hint?: string } = $props();
  const maximum = $derived(Math.max(1, ...bands.map(band => band.count)));
</script>

<div class="histogram" role="img" aria-label={bands.map(b=>`${b.low.toFixed(1)}–${b.high.toFixed(1)}: ${b.count}`).join(', ')}>
  {#each bands as band}<div class="bin" title={`${band.low.toFixed(1)}–${band.high.toFixed(1)}: ${band.count}`}><span style:height={`${band.count/maximum*100}%`}></span></div>{/each}
</div>
<div class="axis"><span>−1</span><span>0</span><span>1</span></div>
<p>{t('uSimilarityHint')}</p>
<style>
  .histogram{height:130px;display:flex;gap:3px;border-bottom:1px solid rgb(var(--stone-500))}
  .bin{flex:1;display:flex;align-items:flex-end}.bin span{display:block;width:100%;background:rgb(var(--primary-700))}
  .axis{display:flex;justify-content:space-between;font-size:14px}p{margin:12px 0}
</style>
