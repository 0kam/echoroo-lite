<script lang="ts">
  import { isTauri } from '@tauri-apps/api/core';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { chooseDirectory, chooseFile } from './files';
  import { commonName, type Species } from './species';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { fill, modelName, type SpeciesAI } from './workflow';
  // Material from other sites, and the species AI as one file to hand to another PC or person.
  type Seen = { species: Species; engine: string; main_engine: string; warning: string | null; round: number; made_at: string | null;
    training: { positive_5s: number; negative_5s: number; other_sites: { positive_5s: number; negative_5s: number; sites: number } | null }; site: string | null;
    tests: { sounds: { heard: number; not_heard: number }; rounds: { round: number; found: number | null; false: number | null; current: boolean }[] } | null };
  let { datasetId, speciesId, info, onchange }: { datasetId: number; speciesId: string; info: SpeciesAI; onchange: () => void | Promise<void> } = $props();
  const sharing = $derived(info.sharing), model = $derived(info.model);
  const here = $derived(info.examples.positive_5s + info.examples.negative_5s);
  const scope = createTask(async (all: boolean) => { await api('/custom-models/sharing', { dataset_id: datasetId, species_id: speciesId, all_sites: all }); await onchange(); }, { toast: true });
  let folder = $state(''), file = $state(''), hide = $state(false), saved = $state(''), seen = $state<Seen | null>(null), taken = $state(false);
  const out = createTask(async () => { saved = '';
    if (isTauri()) { const path = await chooseDirectory(); if (!path) return; folder = path; }
    saved = (await api<{ path: string }>(`/custom-models/${model!.id}/export`, { folder, hide_site: hide })).path; });
  const look = createTask(async () => { seen = null; taken = false;
    if (isTauri()) { const path = await chooseFile(t('shFileKind'), ['json']); if (!path) return; file = path; }
    seen = await api<Seen>('/custom-models/import', { dataset_id: datasetId, path: file, inspect: true }); });
  const take = createTask(async () => { await api('/custom-models/import', { dataset_id: datasetId, path: file, species_id: speciesId, start_date: info.start_date, end_date: info.end_date });
    seen = null; taken = true; await onchange(); });
  const name = (species: Species) => commonName(species, app.locale) || species.scientific_name;
  const score = $derived(seen?.tests?.rounds.find(row => row.current && row.found != null));
</script>
{#if sharing && (sharing.sites || sharing.all_sites)}<section class="panel share" aria-labelledby="share-material"><h2 id="share-material">{t('shMaterial')}</h2>
  <div class="choices" role="radiogroup" aria-labelledby="share-material">
    <label><input type="radio" name="material" checked={!sharing.all_sites} disabled={scope.pending} onchange={() => scope.run(false)} /> {fill('shHere', { n: here })}</label>
    <label><input type="radio" name="material" checked={sharing.all_sites} disabled={scope.pending} onchange={() => scope.run(true)} /> {fill('shAll', { n: here + sharing.positive_5s + sharing.negative_5s, sites: sharing.sites })}</label></div>
  <p class="muted">{fill('shMaterialNote', { positive: sharing.positive_5s, negative: sharing.negative_5s })}{#if info.test && info.test.heard + info.test.not_heard}{' '}{fill('shTestKept', { n: info.test.heard + info.test.not_heard })}{/if}</p>
  {#if sharing.all_sites}<p class="muted">{t('shAllNext')}</p>{/if}
  {#if info.inherited}<p class="muted">{fill('shInheritedToo', { n: info.inherited.positive_5s + info.inherited.negative_5s, sites: info.inherited.sites.join('・') })}</p>{/if}
</section>{/if}
<details class="panel share"><summary>{t('shPass')}</summary>
  <p class="muted">{t('shPassNote')}</p>
  <h3>{t('shOut')}</h3>
  {#if model}
    <label class="check"><input type="checkbox" bind:checked={hide} /> {t('shHide')}</label>
    {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} disabled={out.pending} /></label>{/if}
    <div class="actions"><Button pending={out.pending} disabled={!isTauri() && !folder} onclick={() => out.run()}>{t('shOutButton')}</Button></div>
    {#if saved}<p class="info" role="status">{fill('shSaved', { path: saved })}</p>{/if}
    {#if out.error}<p class="error" role="alert">{t(out.error)}</p>{/if}
  {:else}<p class="muted">{t('shOutNone')}</p>{/if}
  <h3>{t('shIn')}</h3>
  {#if !isTauri()}<label>{t('shFilePath')}<input bind:value={file} disabled={look.pending || take.pending} /></label>{/if}
  <div class="actions"><Button pending={look.pending} disabled={take.pending || (!isTauri() && !file)} onclick={() => look.run()}>{t('shInButton')}</Button></div>
  {#if look.error}<div class="notice" role="alert"><strong>{t('shCannot')}</strong><p>{t(look.error)}</p></div>{/if}
  {#if seen}<div class="notice" role="status">
    {#if seen.species.id !== speciesId}<strong>{t('shCannot')}</strong><p>{fill('ai_file_other_species_named', { name: name(seen.species) })}</p>
    {:else}
      <strong>{t(seen.warning ? 'shWarnTitle' : 'shSeenTitle')}</strong>
      <p>{fill('shSeen', { name: name(seen.species), positive: seen.training.positive_5s, negative: seen.training.negative_5s, n: seen.round })}{#if seen.training.other_sites}{' '}{fill('shSeenPooled', { n: seen.training.other_sites.positive_5s + seen.training.other_sites.negative_5s })}{/if}{#if seen.site}{' '}{fill('shSeenSite', { site: seen.site })}{/if}
        {#if score && seen.tests}{' '}{fill('shSeenTests', { heard: seen.tests.sounds.heard, found: score.found ?? 0, not_heard: seen.tests.sounds.not_heard, false: score.false ?? 0 })}{/if}</p>
      {#if seen.warning}<p>{fill('shWarnText', { from: modelName(seen.engine), here: modelName(seen.main_engine) })}</p>{/if}
      <p class="muted">{t('shSeenNote')}</p>
      <div class="actions"><Button variant="primary" pending={take.pending} onclick={() => take.run()}>{t('shTake')}</Button><Button disabled={take.pending} onclick={() => seen = null}>{t('maCancel')}</Button></div>
      {#if take.error}<p class="error" role="alert">{t(take.error)}</p>{/if}
    {/if}</div>{/if}
  {#if taken}<p class="info" role="status">{t('shTaken')}</p>{/if}
</details>
<style>.share p{margin:12px 0}.choices{display:flex;flex-direction:column;gap:4px}.choices label,.check{display:flex;align-items:center;gap:8px;min-height:44px;font-weight:400}
  .share h3{margin:16px 0 4px}summary{min-height:44px;padding:10px 0;cursor:pointer}.notice{margin:12px 0;padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px}.notice p{margin:8px 0}
  .share input:not([type]){display:block;width:100%;max-width:560px}</style>
