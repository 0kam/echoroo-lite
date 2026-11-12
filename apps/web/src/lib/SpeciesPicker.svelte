<script lang="ts">
  import { onDestroy } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { commonName, type Species } from './species';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { focusMissing, missingText } from './workflow';
  // Case and spacing variants of one scientific name are the same species.
  const norm = (name: string) => name.trim().replace(/\s+/g, ' ').toLowerCase();
  // `invalid`: a note shown under the name field when the page needs a species before it can go on.
  let { value = $bindable(null), disabled = false, label, invalid = '' }: { value: Species | null; disabled?: boolean; label?: string; invalid?: string } = $props();
  const invalidId = $props.id();
  let query = $state(''), rows = $state<Species[]>([]), busy = $state(false), error = $state(''), searched = $state(false);
  let scientific=$state(''),common=$state(''),adding=$state(false),tried=$state(false),existing=$state<Species|null>(null);
  let naming=$state(false),newName=$state(''),triedName=$state(false);
  const noScientific=$derived(tried&&!scientific.trim());
  onDestroy(()=>{clearTimeout(timer);version++;});
  async function add(){adding=true;error='';try{
    const same=(await api<Species[]>(`/species?q=${encodeURIComponent(norm(scientific))}`)).find(row=>norm(row.scientific_name)===norm(scientific));
    if(same)existing=same;else choose(await api<Species>('/species',{scientific_name:scientific,[app.locale]:common||query}));
  }catch(e){error=e instanceof Error?e.message:'unknown_error';}finally{adding=false;}}
  // Only the current language is sent, so a name saved in the other language stays.
  async function saveName(){if(!newName.trim()){triedName=true;return;}adding=true;error='';
    try{value=await api<Species>('/species',{scientific_name:value!.scientific_name,[app.locale]:newName});naming=false;newName='';triedName=false;}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}finally{adding=false;}}
  let timer: ReturnType<typeof setTimeout> | undefined, version = 0;
  function schedule() {
    clearTimeout(timer);
    if (!query.trim()) { rows = []; searched = false; version++; busy = false; return; }
    version++;busy=false;searched=false;rows=[];
    timer = setTimeout(search, 250); // Search as you type, after a short pause.
  }
  async function search() {
    clearTimeout(timer);
    const current = ++version;
    busy = true; error = '';
    try { const data = await api<Species[]>(`/species?q=${encodeURIComponent(query.trim())}`); if (current === version) { rows = data; searched = true; } }
    catch (e) { if (current === version) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if (current === version) busy = false; }
  }
  function choose(row: Species) { value = row; query = ''; rows = []; searched = false; naming = false; }
</script>

{#if value}
  <div class="picked">
    <span><strong>{commonName(value, app.locale) || t('commonNameMissing')}</strong> · <i>{value.scientific_name}</i></span>
    <span class="actions">{#if !value.common_names[app.locale] && !naming && !disabled}<button type="button" class="button sm" onclick={() => { naming = true; }}>{t('spAddName')}</button>{/if}
    <button type="button" class="button sm" {disabled} onclick={() => { value = null; }}>{t('changeSpecies')}</button></span>
  </div>
  {#if naming && !disabled}<form class="stack" novalidate onsubmit={e => { e.preventDefault(); void saveName(); }}>
    <label>{t('wCommonName')}<input bind:value={newName} maxlength="200" disabled={adding} aria-invalid={(triedName && !newName.trim()) || undefined} aria-describedby={triedName && !newName.trim() ? `${invalidId}-name` : undefined} /></label>
    {#if triedName && !newName.trim()}<p class="field-error" id={`${invalidId}-name`}>{t('uEnterCommonName')}</p>{/if}
    <Button type="submit" variant="primary" pending={adding}>{t('saveNames')}</Button></form>{/if}
{:else}
  <label>{label ?? t('targetSpecies')}
    <input bind:value={query} oninput={schedule} onkeydown={e => { if (e.key === 'Enter') { e.preventDefault(); void search(); } }} {disabled} autocomplete="off"
      aria-invalid={!!invalid || undefined} aria-describedby={invalid ? invalidId : undefined} />
  </label>
  {#if invalid}<p class="field-error" id={invalidId}>{invalid}</p>{/if}
  {#if busy}<p class="muted state"><Spinner size={12} /> {t('loading')}</p>{/if}
  {#if searched}
    <div class="name-matches">
      {#each rows as row (row.id)}<button type="button" {disabled} onclick={() => choose(row)}>{commonName(row, app.locale) || t('commonNameMissing')} <i>{row.scientific_name}</i></button>
      {:else}<p class="muted pad">{t('noSpeciesMatch')}</p>{/each}
    </div>
    {#if !rows.length}<details><summary>{t('wAddUnlisted')}</summary><form class="stack" novalidate onsubmit={e=>{e.preventDefault();if(scientific.trim())void add();else{tried=true;void focusMissing(e.currentTarget);}}}><label>{t('scientificName')}<input bind:value={scientific} required maxlength="200" disabled={adding} aria-invalid={noScientific||undefined} aria-describedby={noScientific?`${invalidId}-scientific`:undefined}/></label>
      {#if noScientific}<p class="field-error" id={`${invalidId}-scientific`}>{t('uEnterScientific')}</p>{/if}
      {#if existing && norm(existing.scientific_name) === norm(scientific)}{@const found = existing}<p class="info" role="status">{t('spExists')} <i>{found.scientific_name}</i></p>
        <Button onclick={() => { choose(found); naming = !found.common_names[app.locale]; newName = common; }}>{t('spChooseExisting')}</Button>{/if}<label>{t('wCommonName')}<input bind:value={common} placeholder={query} maxlength="200" disabled={adding}/></label><Button type="submit" variant="primary" pending={adding} {disabled}>{t('addSpecies')}</Button>
      {#if !scientific.trim()}<p class={tried?'field-error':'muted'}>{missingText(['scientificName'])}</p>{/if}</form></details>{/if}
  {/if}
{/if}
{#if error}<p class="error" role="alert">{t(error)}</p>{/if}

<style>
  .picked { display: flex; align-items: center; justify-content: space-between; gap: 12px; flex-wrap: wrap; padding: 10px 12px; border: 1px solid rgb(var(--primary-200)); background: rgb(var(--primary-100)); border-radius: 6px; }
  :global(.dark) .picked { background: rgb(var(--stone-200)); border-color: rgb(var(--stone-300)); }
  .state { margin-top: 6px; }
  .pad { padding: 8px 12px; }
</style>
