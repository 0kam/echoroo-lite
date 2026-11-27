<script lang="ts">
  import { isTauri } from '@tauri-apps/api/core';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { chooseFile } from './files';
  import { commonName, type Species } from './species';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { fill, modelName, versionName, type AIVersions, type CustomModel, type TestScore } from './workflow';
  // An AI of another site on this PC, or from a file, tried at this site: it is copied here and becomes the site's
  // AI, and the test sounds here are then made with it. `range`: the page's dates as a query.
  type Seen = { species: Species; engine: string; main_engine: string; warning: string | null; round: number; site: string | null;
    training: { positive_5s: number; negative_5s: number; other_sites: { positive_5s: number; negative_5s: number } | null };
    tests: { sounds: { heard: number; not_heard: number }; rounds: { found: number | null; false: number | null; current: boolean }[] } | null };
  let { datasetId, speciesId, speciesName, range = '', ontaken, shown = $bindable(false) }: { datasetId: number; speciesId: string; speciesName: string; range?: string; ontaken: (model: CustomModel) => void | Promise<void>; shown?: boolean } = $props();
  // Offered only while the site has no AI for the species: a site's AI is changed from its list of AIs, never here.
  let others = $state<AIVersions['others']>([]), file = $state(''), seen = $state<Seen | null>(null), ready = $state(false), owned = $state(false);
  $effect(() => { const chosen = speciesId; others = []; seen = null; ready = false; owned = false;
    api<AIVersions>(`/datasets/${datasetId}/species/${encodeURIComponent(chosen)}/ai-versions?${range}`).then(value => { if (chosen === speciesId) { others = value.others; owned = value.current_id != null; ready = true; } },
      () => { if (chosen === speciesId) ready = true; /* Without the list the file import still works. */ }); });
  $effect(() => { shown = ready && !owned; });
  const dates = () => Object.fromEntries(new URLSearchParams(range));
  const tryIt = createTask(async (id: number) => { await ontaken(await api<CustomModel>(`/custom-models/${id}/use`, { dataset_id: datasetId, ...dates() })); });
  const look = createTask(async () => { seen = null;
    if (isTauri()) { const path = await chooseFile(t('shFileKind'), ['json']); if (!path) return; file = path; }
    seen = await api<Seen>('/custom-models/import', { dataset_id: datasetId, path: file, inspect: true, ...dates() }); });
  const take = createTask(async () => { await ontaken(await api<CustomModel>('/custom-models/import', { dataset_id: datasetId, path: file, species_id: speciesId, ...dates() })); });
  const name = (species: Species) => commonName(species, app.locale) || species.scientific_name;
  const there = (site: string, score: TestScore) => fill('vbThere', { site, ...score });
  const score = $derived(seen?.tests?.rounds.find(row => row.current && row.found != null));
</script>
{#snippet fromFile()}<details><summary>{t('vbFile')}</summary>
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
      <p class="muted">{t('vbSeenNote')}</p>
      <div class="actions"><Button variant="primary" pending={take.pending} onclick={() => take.run()}>{t('vbUseFile')}</Button><Button disabled={take.pending} onclick={() => seen = null}>{t('maCancel')}</Button></div>
      {#if take.error}<p class="error" role="alert">{t(take.error)}</p>{/if}
    {/if}</div>{/if}
</details>{/snippet}
{#if !ready || owned}{:else if others.length}<section class="panel borrow" aria-labelledby="borrow-title"><h2 id="borrow-title">{t('vbTitle')}</h2>
  <p>{fill('vbText', { name: speciesName })}</p>
  {#each others as other (other.id)}<div class="ai"><div><strong>{fill('vbAI', { version: versionName(other) })}</strong><br>
      <span class="muted">{fill('vbFacts', { positive: other.training.positive_5s, negative: other.training.negative_5s })}
        ・{other.score && other.site ? there(other.site, other.score) : t('vbNoTests')}{#if other.warning} ・{t('vlWarn')}{/if}</span></div>
    <Button variant={others.length === 1 ? 'primary' : 'default'} pending={tryIt.pending} onclick={() => tryIt.run(other.id)}>{t('vbTry')}</Button></div>{/each}
  <p class="muted">{t('vbTryNote')}</p>
  {#if tryIt.error}<p class="error" role="alert">{t(tryIt.error)}</p>{/if}
  {@render fromFile()}
</section>{:else}<div class="borrow">{@render fromFile()}</div>{/if}
<style>.borrow p{margin:12px 0}.ai{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  summary{min-height:44px;padding:10px 0;cursor:pointer}.notice{margin:12px 0;padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px}.notice p{margin:8px 0}
  .borrow input:not([type]){display:block;width:100%;max-width:560px}</style>
