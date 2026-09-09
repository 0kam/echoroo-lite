<script lang="ts">
  import { api } from './api';
  import { commonName, type Species } from './species';
  let { value = $bindable(null), locale, t, disabled = false }: { value: Species | null; locale: string;
    t: (key: string) => string; disabled?: boolean } = $props();
  let query = $state(''), rows = $state<Species[]>([]), busy = $state(false), error = $state(''), searched = $state(false);
  async function search() {
    busy = true; error = '';
    try { rows = await api<Species[]>(`/species?q=${encodeURIComponent(query)}`); searched = true; }
    catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
</script>

{#if value}
  <div class="section-heading"><span><strong>{commonName(value, locale) || t('commonNameMissing')}</strong> · <i>{value.scientific_name}</i></span>
    <button class="small" {disabled} onclick={() => { value = null; searched = false; }}>{t('changeSpecies')}</button></div>
{:else}
  <form onsubmit={e => { e.preventDefault(); void search(); }}>
    <label>{t('targetSpecies')}<input bind:value={query} required {disabled} /></label><button disabled={disabled || busy}>{t('search')}</button>
  </form>
  {#if searched}<div class="name-matches">{#each rows as row}<button {disabled} onclick={() => value = row}>
    {commonName(row, locale) || t('commonNameMissing')} <i>{row.scientific_name}</i></button>
    {:else}<p class="muted">{t('chooseSpeciesMissing')}</p>{/each}</div>{/if}
{/if}
{#if error}<p class="error" role="alert">{t(error)}</p>{/if}

<style>
  form { display: flex; align-items: end; gap: 8px; }
  label { flex: 1; }
</style>
