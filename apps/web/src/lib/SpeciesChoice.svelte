<script lang="ts">
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import type { Choices, Taxon } from './heard';
  import { familyLabel, speciesName } from './rollups';
  import { fill } from './workflow';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  // Choosing the species of a sound: species by name, with a mark on those not expected here, and families
  // ("a bird of family X") for when the species cannot be told. `exclude`: ids that cannot be chosen.
  // `detections`: also say how many of the site's sounds the AI detected each choice in (the sound map's "by species").
  let { datasetId, onchoose, exclude = [], pending = false, detections = false }: { datasetId: number; onchoose: (taxon: Taxon) => void; exclude?: string[]; pending?: boolean; detections?: boolean } = $props();
  const inputId = $props.id();
  let query = $state(''), found = $state<Choices | null>(null), busy = $state(false), error = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined, version = 0;
  function schedule() { clearTimeout(timer); version++; found = null; busy = false; if (query.trim()) timer = setTimeout(search, 250); }
  async function search() {
    clearTimeout(timer);
    const current = ++version;
    busy = true; error = '';
    try { const data = await api<Choices>(`/datasets/${datasetId}/heard/species?q=${encodeURIComponent(query.trim())}${detections ? '&detections=true' : ''}`); if (current === version) found = data; }
    catch (e) { if (current === version) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if (current === version) busy = false; }
  }
  const species = $derived(found?.species.filter(row => !exclude.includes(row.id)) ?? []);
  const families = $derived(found?.families.filter(row => !exclude.includes(row.id)) ?? []);
</script>
<div class="choice">
  <label for={inputId}>{t('hdPickLabel')}</label>
  <input id={inputId} bind:value={query} oninput={schedule} onkeydown={e => { if (e.key === 'Enter') { e.preventDefault(); void search(); } }} autocomplete="off" disabled={pending} />
  <p class="muted">{t('hdPickHint')}</p>
  {#if busy}<p class="muted"><Spinner size={12} /> {t('loading')}</p>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#snippet detected(row: Taxon & { detected?: number | null })}{#if row.detected != null}<span class="count">{row.detected ? fill('hdDetected', { n: row.detected }) : t('hdDetectedNone')}</span>{/if}{/snippet}
  {#if found}
    {#snippet familyRows()}{#if families.length}<h4>{t('hdFamilyHead')}</h4>
      <ul>{#each families as row (row.id)}<li><span><strong>{familyLabel(row)}</strong> <i>{row.scientific_name}</i>{@render detected(row)}</span>
        <Button size="sm" disabled={pending} onclick={() => onchoose({ ...row, rank: 'family' })}>{t('hdChoose')}</Button></li>{/each}</ul>{/if}{/snippet}
    <!-- With no bird of the Japan checklist among the matches, the family is the likelier choice and comes first. -->
    {#if !species.some(row => row.japan)}{@render familyRows()}{/if}
    {#if species.length}{#if !species.some(row => row.japan) && families.length}<h4>{t('hdSpeciesHead')}</h4>{/if}<ul>{#each species as row (row.id)}<li><span><strong>{speciesName(row)}</strong> <i>{row.scientific_name}</i>
      {#if row.japan === false}<span class="mark">{t('hdNotJapan')}</span>{:else if row.rare}<span class="mark">{t('hdRare')}</span>{/if}{@render detected(row)}</span>
      <Button size="sm" disabled={pending} onclick={() => onchoose(row)}>{t('hdChoose')}</Button></li>{/each}</ul>
    {:else if !families.length}<p class="muted">{t('hdNoSpecies')}</p>{/if}
    {#if species.some(row => row.japan)}{@render familyRows()}{/if}
  {/if}
</div>
<style>.choice{display:flex;flex-direction:column;gap:8px}.choice p,.choice h4{margin:0}ul{list-style:none;margin:0;padding:0;max-height:260px;overflow-y:auto;border:1px solid rgb(var(--stone-300));border-radius:8px}
  li :global(.button){flex-shrink:0;white-space:nowrap}li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 12px}li+li{border-top:1px solid rgb(var(--stone-300))}
  .count{display:block;font-size:14px;color:rgb(var(--stone-600))}
  .mark{display:inline-block;margin-left:8px;padding:0 8px;border:1px solid rgb(var(--stone-400));border-radius:999px;font-size:13px;white-space:nowrap}</style>
