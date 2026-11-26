<script lang="ts">
  import { resolve } from '$app/paths';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { taxonName, type Other, type Taxon } from './heard';
  import SpeciesChoice from './SpeciesChoice.svelte';
  import Button from './ui/Button.svelte';
  import { fill } from './workflow';
  // "Another species is calling too", under a question about one species (`of`) in one sound. Each chosen species is
  // recorded as heard in this sound and stays listed here; one added on a question card can be taken back.
  let { datasetId, of, recordingId, start, end, screen }:
    { datasetId: number; of: string; recordingId: number; start: number; end: number; screen: 'validation' | 'training' | 'species_mark' } = $props();
  let others = $state<Other[]>([]), picking = $state(false), busy = $state(false), error = $state('');
  const sound = $derived(`recording_id=${recordingId}&start_s=${start}&end_s=${end}`);
  $effect(() => { const query = sound; others = []; picking = false; error = '';
    api<Other[]>(`/datasets/${datasetId}/heard/others?${query}&of=${encodeURIComponent(of)}`).then(value => { if (query === sound) others = value; }, () => {}); });
  async function set(taxon: Taxon, on: boolean) {
    busy = true; error = '';
    try { others = await api<Other[]>('/heard/others', { dataset_id: datasetId, recording_id: recordingId, start_s: start, end_s: end, of, species_id: taxon.id, screen, on }); picking = false; }
    catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
</script>
<div class="others">
  <div class="line"><Button size="sm" active={picking} aria-expanded={picking} onclick={() => picking = !picking}>{t('hdOthersAdd')}</Button>
    {#each others as other (other.species.id)}<span class="chip" title={other.removable ? undefined : t('hdOthersElsewhere')}>{taxonName(other.species)}
      {#if other.removable}<button type="button" aria-label={fill('hdOthersRemove', { name: taxonName(other.species) })} disabled={busy} onclick={() => set(other.species, false)}>✕</button>{/if}</span>{/each}</div>
  {#if picking}<div class="picker"><h3>{t('hdOthersTitle')}</h3>
    <SpeciesChoice {datasetId} exclude={[of, ...others.map(other => other.species.id)]} pending={busy} onchoose={taxon => set(taxon, true)} />
    <p class="muted">{t('hdOthersNote')}</p>
    <p class="muted">{t('hdOthersFixLater')} <a href={resolve(`/datasets/${datasetId}/heard`)}>{t('hdTitle')}</a></p>
    <div><Button size="sm" onclick={() => picking = false}>{t('hdClose')}</Button></div></div>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
</div>
<style>.others{display:flex;flex-direction:column;gap:8px;text-align:left}.line{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .chip{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border:1px solid rgb(var(--stone-400));border-radius:999px;font-size:14px}
  .chip button{border:0;background:none;color:inherit;cursor:pointer;min-width:28px;min-height:28px;font:inherit}
  .picker{display:flex;flex-direction:column;gap:8px;padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px;background:rgb(var(--color-card-bg))}.picker h3,.picker p{margin:0}</style>
