<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api';
  import { commonName, type Species } from './species';
  import type { AudioSource } from './AudioRegion.svelte';
  export type Attribution = { id: number; scientific_name: string; common_name: string; recordist: string;
    country: string; location: string; quality: string; length: string; type: string; date: string; license: string; url: string };
  let { species, locale, t, onselect }: { species: Species | null; locale: string; t: (key: string) => string;
    onselect: (source: AudioSource & { attribution?: Attribution }) => void } = $props();
  let configured = $state(false), key = $state(''), country = $state('Japan'), quality = $state('A');
  let busy = $state(false), error = $state(''), keySaved = $state(false);
  let results = $state<{ recordings: Attribution[]; total_recordings: number; total_pages: number; page: number } | null>(null);
  let lastQuery: { species_id: string; country: string; quality: string } | null = null;
  async function action(fn: () => Promise<void>) {
    busy = true; error = '';
    try { await fn(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
  async function save() {
    configured = (await api<{ configured: boolean }>('/xeno-canto/key', { key: key.trim() })).configured;
    key = ''; keySaved = true;
  }
  async function search(page = 1, fresh = false) {
    if (fresh && species) lastQuery = { species_id: species.id, country, quality };
    if (!lastQuery) return;
    results = await api(`/xeno-canto/search?${new URLSearchParams({ ...lastQuery, page: String(page) })}`);
  }
  onMount(() => { void api<{ configured: boolean }>('/xeno-canto/key').then(r => configured = r.configured).catch(e => error = e.message); });
</script>

<details class="xc-panel"><summary>{t('xcFind')}</summary>
  <details open={!configured}><summary>{t('xcSettings')} · {t(configured ? 'xcConfigured' : 'xcNotConfigured')}</summary>
    <p class="muted">{t('xcKeyHint')} <a href="https://xeno-canto.org/account" target="_blank" rel="noreferrer">Xeno-Canto</a></p>
    <form onsubmit={e => { e.preventDefault(); void action(save); }}>
      <label>{t('xcKey')}<input type="password" autocomplete="off" bind:value={key} maxlength="500" /></label>
      <button class="small" disabled={busy}>{t('xcSaveKey')}</button>
      {#if keySaved}<span class="saved" role="status">{t('xcKeySaved')}</span>{/if}
    </form>
  </details>
  <p class="muted">{species ? `${commonName(species, locale)} · ${species.scientific_name}` : t('chooseTargetFirst')}</p>
  <form onsubmit={e => { e.preventDefault(); void action(() => search(1, true)); }}>
    <div class="form-grid"><label>{t('xcCountry')}<input bind:value={country} maxlength="100" /></label>
      <label>{t('xcQuality')}<select bind:value={quality}><option value="">{t('allQualities')}</option>{#each ['A','B','C','D','E'] as q}<option>{q}</option>{/each}</select></label></div>
    <button disabled={!species || !configured || busy}>{t(busy ? 'xcLoading' : 'xcSearch')}</button>
  </form>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if results}
    <p class="muted">{results.total_recordings} {t('files')} · {t('page')} {results.page} / {results.total_pages}</p>
    <div class="xc-results">{#each results.recordings as row}
      <article><div class="section-heading"><strong>XC{row.id} · {row.quality} · {row.length}</strong>
        <button class="small" disabled={busy} onclick={() => action(async () => onselect(await api(`/xeno-canto/${row.id}/import`, {})))}>{t('xcSelect')}</button></div>
        <p>{species?.scientific_name === row.scientific_name ? commonName(species, locale) : locale === 'en' ? row.common_name : t('commonNameMissing')} · <i>{row.scientific_name}</i></p>
        <p class="muted">{row.recordist} · {row.country} / {row.location} · {row.type} · {row.date}</p>
        <p class="muted">{row.license} · <a href={row.url} target="_blank" rel="noreferrer">XC{row.id}</a></p>
      </article>{:else}<p class="empty">{t('xcNoResults')}</p>{/each}</div>
    <div class="section-heading"><button class="small" disabled={busy || results.page <= 1} onclick={() => action(() => search(results!.page - 1))}>{t('previousView')}</button>
      <button class="small" disabled={busy || results.page >= results.total_pages} onclick={() => action(() => search(results!.page + 1))}>{t('nextView')}</button></div>
  {/if}
</details>

<style>
  .xc-panel { margin: 12px 0; border: 1px solid rgb(var(--stone-300)); border-radius: 6px; padding: 12px; }
  details details, form { margin: 12px 0; }
  .xc-results { max-height: 320px; overflow-y: auto; }
  article { padding: 12px 4px; border-bottom: 1px solid rgb(var(--stone-300)); font-size: 12px; }
  a { color: rgb(var(--primary-700)); overflow-wrap: anywhere; }
</style>
