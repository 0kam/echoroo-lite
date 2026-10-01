<script lang="ts">
  import { api } from '$lib/api.svelte';
  import { app, t } from '$lib/app.svelte';
  import { commonName, type Species } from '$lib/species';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  let { onupdate = async () => {} }: { onupdate?: () => Promise<void> } = $props();
  let query = $state(''), scientific = $state(''), japanese = $state(''), english = $state('');
  let matches = $state<Species[]>([]), selected = $state<Species | null>(null);
  let busy = $state(false), error = $state(''), searched = $state(false);
  let timer: ReturnType<typeof setTimeout> | undefined, version = 0;

  function choose(row: Species) {
    selected = row; scientific = row.scientific_name;
    japanese = row.custom_names.ja || ''; english = row.custom_names.en || '';
  }
  function reset() {
    selected = null; scientific = ''; japanese = ''; english = '';
  }
  function schedule() {
    clearTimeout(timer);
    if (!query.trim()) { matches = []; searched = false; version++; busy = false; return; }
    timer = setTimeout(search, 250);
  }
  async function search() {
    clearTimeout(timer);
    const current = ++version;
    busy = true; error = '';
    try {
      const rows = await api<Species[]>(`/species?q=${encodeURIComponent(query.trim())}`);
      if (current === version) { matches = rows; searched = true; }
    } catch (e) { if (current === version) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if (current === version) busy = false; }
  }
  const save = createTask(async () => {
    const row = await api<Species>('/species', { scientific_name: scientific, ja: japanese, en: english });
    choose(row); await onupdate(); notify('namesSaved');
    if (searched) await search();
  }, { toast: true });
</script>

<section class="panel species-settings">
  <h2>{t('speciesNames')}</h2><p class="muted">{t('speciesNamePolicy')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <form class="inline" onsubmit={event => { event.preventDefault(); void search(); }}>
    <label>{t('findSpecies')}<input bind:value={query} oninput={schedule} required /></label><Button type="submit" pending={busy}>{t('search')}</Button>
  </form>
  {#if searched}
    <div class="name-matches">
      {#each matches as row}<button class:chosen={selected?.id === row.id} onclick={() => choose(row)}>
        {commonName(row, app.locale) || t('commonNameMissing')} <i>{row.scientific_name}</i>
      </button>{:else}<p class="muted">{t('noSpeciesMatch')}</p>{/each}
    </div>
  {/if}
  <form class="stack names" onsubmit={event => { event.preventDefault(); save.run(); }}>
    <div class="section-heading"><h3>{t(selected ? 'editSpeciesName' : 'addSpecies')}</h3>{#if selected}<Button type="button" size="sm" variant="ghost" onclick={reset}>{t('newSpecies')}</Button>{/if}</div>
    <div class="form-grid">
      <label>{t('scientificName')}<input bind:value={scientific} required maxlength="200" readonly={!!selected} /></label>
      <label>{t('japaneseName')}<input bind:value={japanese} maxlength="200" placeholder={selected?.common_names.ja || ''} /></label>
      <label>{t('englishName')}<input bind:value={english} maxlength="200" placeholder={selected?.common_names.en || ''} /></label>
    </div>
    <p class="muted">{t('nameOverrideHint')}</p>
    <Button type="submit" variant="primary" pending={save.pending}>{t('saveNames')}</Button>
    {#if save.error}<p class="error" role="alert">{t(save.error)}</p>{/if}
  </form>
</section>

<style>
  .names { margin-top: 24px; padding-top: 24px; border-top: 1px solid rgb(var(--stone-200)); }
</style>
