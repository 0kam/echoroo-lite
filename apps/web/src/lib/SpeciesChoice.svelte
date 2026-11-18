<script lang="ts">
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import type { Choices, Taxon } from './heard';
  import { familyLabel, speciesName } from './rollups';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  // Choosing the species of a sound: species by name, with a mark on those not expected here, and families
  // ("a bird of family X") for when the species cannot be told. `exclude`: ids that cannot be chosen.
  let { datasetId, onchoose, exclude = [], pending = false }: { datasetId: number; onchoose: (taxon: Taxon) => void; exclude?: string[]; pending?: boolean } = $props();
  const inputId = $props.id();
  let query = $state(''), found = $state<Choices | null>(null), busy = $state(false), error = $state('');
  let timer: ReturnType<typeof setTimeout> | undefined, version = 0;
  function schedule() { clearTimeout(timer); version++; found = null; busy = false; if (query.trim()) timer = setTimeout(search, 250); }
  async function search() {
    clearTimeout(timer);
    const current = ++version;
    busy = true; error = '';
    try { const data = await api<Choices>(`/datasets/${datasetId}/heard/species?q=${encodeURIComponent(query.trim())}`); if (current === version) found = data; }
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
  {#if found}
    {#if species.length}<ul>{#each species as row (row.id)}<li><span><strong>{speciesName(row)}</strong> <i>{row.scientific_name}</i>
      {#if row.japan === false}<span class="mark">{t('hdNotJapan')}</span>{:else if row.rare}<span class="mark">{t('hdRare')}</span>{/if}</span>
      <Button size="sm" disabled={pending} onclick={() => onchoose(row)}>{t('hdChoose')}</Button></li>{/each}</ul>
    {:else}<p class="muted">{t('noSpeciesMatch')}</p>{/if}
    {#if families.length}<h4>{t('hdFamilyHead')}</h4>
      <ul>{#each families as row (row.id)}<li><span><strong>{familyLabel(row)}</strong> <i>{row.scientific_name}</i></span>
        <Button size="sm" disabled={pending} onclick={() => onchoose({ ...row, rank: 'family' })}>{t('hdChoose')}</Button></li>{/each}</ul>{/if}
  {/if}
</div>
<style>.choice{display:flex;flex-direction:column;gap:8px}.choice p,.choice h4{margin:0}ul{list-style:none;margin:0;padding:0;max-height:260px;overflow-y:auto;border:1px solid rgb(var(--stone-300));border-radius:8px}
  li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 12px}li+li{border-top:1px solid rgb(var(--stone-300))}
  .mark{display:inline-block;margin-left:8px;padding:0 8px;border:1px solid rgb(var(--stone-400));border-radius:999px;font-size:13px;white-space:nowrap}</style>
