<script lang="ts">
  import { api } from '$lib/api';
  import { commonName, type Species } from '$lib/species';
  let { locale, t, onupdate }: { locale: string; t: (key: string) => string; onupdate: () => Promise<void> } = $props();
  let query = $state(''), scientific = $state(''), japanese = $state(''), english = $state('');
  let matches = $state<Species[]>([]), selected = $state<Species | null>(null);
  let busy = $state(false), error = $state(''), saved = $state(false), searched = $state(false);

  async function action(fn: () => Promise<void>) {
    busy = true; error = ''; saved = false;
    try { await fn(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
  function choose(row: Species) {
    selected = row; scientific = row.scientific_name;
    japanese = row.custom_names.ja || ''; english = row.custom_names.en || ''; saved = false;
  }
  function reset() {
    selected = null; scientific = ''; japanese = ''; english = ''; saved = false;
  }
  async function search() {
    matches = await api<Species[]>(`/species?q=${encodeURIComponent(query)}`); searched = true;
  }
  async function save() {
    const row = await api<Species>('/species', { scientific_name: scientific, ja: japanese, en: english });
    choose(row); await onupdate(); saved = true;
    if (searched) await search();
  }
</script>

<section class="panel species-settings">
  <h2>{t('speciesNames')}</h2><p class="muted">{t('speciesNamePolicy')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <form class="name-search" onsubmit={event => { event.preventDefault(); action(search); }}>
    <div class="inline"><label style="flex: 1">{t('findSpecies')}<input bind:value={query} required /></label><button disabled={busy}>{t('search')}</button></div>
  </form>
  {#if searched}
    <div class="name-matches">
      {#each matches as row}<button class:chosen={selected?.id === row.id} onclick={() => choose(row)}>
        {commonName(row, locale) || t('commonNameMissing')} <i>{row.scientific_name}</i>
      </button>{:else}<p class="muted">{t('noSpeciesMatch')}</p>{/each}
    </div>
  {/if}
  <form onsubmit={event => { event.preventDefault(); action(save); }}>
    <div class="section-heading"><h3>{t(selected ? 'editSpeciesName' : 'addSpecies')}</h3><button type="button" class="small" onclick={reset}>{t('addSpecies')}</button></div>
    <div class="form-grid">
      <label>{t('scientificName')}<input bind:value={scientific} required maxlength="200" readonly={!!selected} /></label>
      <label>{t('japaneseName')}<input bind:value={japanese} maxlength="200" placeholder={selected?.common_names.ja || ''} /></label>
      <label>{t('englishName')}<input bind:value={english} maxlength="200" placeholder={selected?.common_names.en || ''} /></label>
    </div>
    <p class="muted">{t('nameOverrideHint')}</p>
    <button class="primary" disabled={busy}>{t('saveNames')}</button>
    {#if saved}<span class="saved" role="status">{t('namesSaved')}</span>{/if}
  </form>
</section>
