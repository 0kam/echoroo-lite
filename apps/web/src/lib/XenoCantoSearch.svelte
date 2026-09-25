<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { commonName, type Species } from './species';
  import type { AudioSource } from './AudioRegion.svelte';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  export type Attribution = { id: number; scientific_name: string; common_name: string; recordist: string;
    country: string; location: string; quality: string; length: string; type: string; date: string; license: string; url: string };
  let { species, onselect }: { species: Species | null; onselect: (source: AudioSource & { attribution?: Attribution }) => void } = $props();
  let configured = $state<boolean | null>(null), country = $state(''), countryFilter = $state(false), quality = $state('A');
  let results = $state<{ recordings: Attribution[]; total_recordings: number; total_pages: number; page: number } | null>(null);
  let lastQuery: { species_id: string; country: string; quality: string } | null = null;
  let importing = $state<number | null>(null), restored=$state(false);
  onMount(()=>{try{const saved=JSON.parse(sessionStorage.getItem(`echoroo-xc-${species?.id}`)||'null');if(saved){country=saved.country;countryFilter=!!saved.country;quality=saved.quality;results=saved.results;lastQuery=saved.lastQuery;}}catch{}restored=true;});
  $effect(()=>{if(restored)sessionStorage.setItem(`echoroo-xc-${species?.id}`,JSON.stringify({country,quality,results,lastQuery}));});
  const licenseName=(value:string)=>{const match=value.match(/creativecommons\.org\/licenses\/([^/]+)\/([^/]+)/);return match?`CC ${match[1].toUpperCase()} ${match[2]}`:value;};
  const search = createTask(async (page: number, fresh = false) => {
    if (fresh && species) lastQuery = { species_id: species.id, country: countryFilter ? country.trim() : '', quality };
    if (!lastQuery) return;
    results = await api(`/xeno-canto/search?${new URLSearchParams({ ...lastQuery, page: String(page) })}`);
  });
  const pick = createTask(async (row: Attribution) => {
    importing = row.id;
    try { onselect(await api(`/xeno-canto/${row.id}/import`, {})); } finally { importing = null; }
  }, { toast: true });
  onMount(() => { void api<{ configured: boolean }>('/xeno-canto/key').then(r => configured = r.configured).catch(() => configured = false); });
</script>

<div class="xc">
  {#if configured === false}
    <p class="muted">{t('xcNotConfigured')} · <a href={resolve('/settings')}>{t('navSettings')}</a></p>
  {/if}
  <p class="muted">{species ? `${commonName(species, app.locale)} · ${species.scientific_name}` : t('chooseTargetFirst')}</p>
  <form class="query" onsubmit={e => { e.preventDefault(); search.run(1, true); }}>
    {#if countryFilter}<label>{t('xcCountry')}<input bind:value={country} maxlength="100" placeholder="Japan" /></label>
      <Button onclick={() => { countryFilter = false; country = ''; }}>{t('xcAllCountries')}</Button>
    {:else}<Button onclick={() => countryFilter = true}>{t('xcFilterCountry')}</Button>{/if}
    <label>{t('xcQuality')}<select bind:value={quality}><option value="">{t('allQualities')}</option>{#each ['A','B','C','D','E'] as q}<option>{q}</option>{/each}</select></label>
    <Button type="submit" pending={search.pending} disabled={!species || !configured}>{t('xcSearch')}</Button>
  </form>
  {#if search.error}<p class="error" role="alert">{t(search.error)}</p>{/if}
  {#if results}
    <p class="muted">{results.total_recordings} {t('files')} · {t('page')} {results.page} / {results.total_pages}</p>
    <div class="xc-results">
      {#each results.recordings as row (row.id)}
        <article>
          <div class="section-heading"><strong>XC{row.id} · {row.quality} · {row.length}</strong>
            <Button size="sm" pending={importing === row.id} disabled={pick.pending} onclick={() => pick.run(row)}>{t('xcSelect')}</Button></div>
          <p>{species?.scientific_name === row.scientific_name ? commonName(species, app.locale) : app.locale === 'en' ? row.common_name : t('commonNameMissing')} · <i>{row.scientific_name}</i></p>
          <p class="muted">{row.recordist} · {row.country} / {row.location} · {row.type}</p>
          <p class="muted"><a href={row.license} target="_blank" rel="noreferrer">{licenseName(row.license)}</a> · <a href={row.url} target="_blank" rel="noreferrer">XC{row.id}</a></p>
        </article>
      {:else}<p class="empty">{t('xcNoResults')}</p>{/each}
    </div>
    {#if results.total_pages > 1}
      <div class="actions">
        <Button size="sm" disabled={search.pending || results.page <= 1} onclick={() => search.run(results!.page - 1)}>‹ {t('previousView')}</Button>
        <Button size="sm" disabled={search.pending || results.page >= results.total_pages} onclick={() => search.run(results!.page + 1)}>{t('nextView')} ›</Button>
      </div>
    {/if}
  {/if}
</div>

<style>
  .xc { display: flex; flex-direction: column; gap: 8px; }
  .query { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; }
  .query label { flex: 1; min-width: 120px; }
  .xc-results { max-height: 480px; overflow-y: auto; border: 1px solid rgb(var(--stone-300)); border-radius: 6px; }
  article { padding: 18px; border-bottom: 1px solid rgb(var(--stone-200)); font-size: 15px; }
  article:last-child { border-bottom: 0; }
  article p { margin-top: 2px; }
  a { overflow-wrap: anywhere; }
</style>
