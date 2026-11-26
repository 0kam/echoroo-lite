<script lang="ts" module>
  import type { Species } from './species';
  /** A suggested group: its sounds are the positions lo..hi-1; `targets` of them match the open way, `left` of those not listened to yet. */
  type Group = { node: number; size: number; lo: number; hi: number; targets: number; left: number };
  /** What the open way puts on the map: the suggested groups (flags), the sounds it points at, and the answers by ear.
      `guess`: the species the AI answered for a target, where known. */
  export type MapOverlay = { entry: 'unsure' | 'species' | 'examples'; marks: [number, 1 | 2][]; targets: number[]; groups: Group[]; guess: Map<number, Species> };
  // The two answers by ear (drawn with ✓ and × as well, so they never rest on colour alone).
  export const HEARD = '#1565c0', NOT_HEARD = '#5b5670';
  type Found = { species?: Taxon; groups: Group[]; scattered: number; marks: [number, 1 | 2][]; targets: number[];
    examples?: number; mixed?: number; guesses?: Species[]; guess?: number[] };
  // The page is made anew for every group opened: the last answer is on the new page from its first drawing, so nothing
  // above the map changes height (the layout restores the scroll position), and is read again behind it.
  let cache: { key: string; found: Found } | null = null;
</script>
<script lang="ts">
  import { untrack } from 'svelte';
  import { resolve } from '$app/paths';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from './api.svelte';
  import { fileName, t } from './app.svelte';
  import AudioRegion, { type AudioSource } from './AudioRegion.svelte';
  import { taxonName, type Taxon } from './heard';
  import { speciesName } from './rollups';
  import SpeciesChoice from './SpeciesChoice.svelte';
  import XenoCantoSearch from './XenoCantoSearch.svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { createTask } from './ui/task.svelte';
  import { fill } from './workflow';
  // Three ways into the sound map. Each asks the app which groups to visit and lists them, lettered as their flags on the map;
  // "look here" goes into the group. What was heard by ear is drawn on the map whichever way is open.
  type Entry = MapOverlay['entry'];
  // Where the example sound comes from: sounds checked by ear (chosen by the app), Xeno-Canto, or an audio file on the PC.
  type From = 'heard' | 'xc' | 'file';
  type Clip = { source: AudioSource; start_s: number; end_s: number };
  let { datasetId, node, go, onoverlay, onspecies }:
    { datasetId: number; node: number; go: (node: number) => void; onoverlay: (overlay: MapOverlay) => void;
      onspecies?: (species: Taxon | null) => void } = $props();
  const key = () => `echoroo-map-entry:${datasetId}`;
  // The page is made anew for every group opened, so the chosen way, species and example are kept for the session.
  const kept = untrack(() => { try { return JSON.parse(sessionStorage.getItem(key()) || 'null'); } catch { return null; } }) as { entry: Entry; species: Taxon | null; from?: From; clip?: Clip | null } | null;
  let entry = $state<Entry>(kept?.entry ?? 'unsure'), species = $state<Taxon | null>(kept?.species ?? null);
  let from = $state<From>(kept?.from ?? 'heard'), clip = $state<Clip | null>(kept?.clip ?? null);
  // An audio file or Xeno-Canto recording whose part is being chosen.
  let source = $state<AudioSource | null>(null), start = $state(0), end = $state(5), referencePath = $state('');
  const outside = $derived(entry === 'examples' && from !== 'heard');
  // What is asked for: the way, the species and the example.
  const asked = () => `${datasetId}:${entry}:${species?.id}:${outside && clip ? [clip.source.kind, clip.source.id, clip.start_s, clip.end_s] : from}`;
  let found = $state<Found | null>(untrack(() => cache?.key === asked() ? cache.found : null)), error = $state(''), busy = $state(false), request = 0, version = $state(0);
  const valid = $derived(!!source && end - start >= .2 - 1e-9 && end - start <= 30 && start >= 0 && end <= source.seconds);
  /** Read the numbers and marks again after an answer was recorded on the page. */
  export function refresh() { version++; }
  let last = '';
  $effect(() => { const which = entry, taxon = species?.id, again = version, part = outside ? clip : null, wait = outside && !clip; untrack(() => {
    try { sessionStorage.setItem(key(), JSON.stringify({ entry, species, from, clip })); } catch { /* Private windows may refuse storage. */ }
    onspecies?.(which === 'unsure' ? null : species);
    // A refresh keeps the list on screen while the new numbers load; another way, species or example starts empty.
    const now = asked(), same = last === now; last = now; void again;
    const show = (value: Found) => { found = value; onoverlay({ entry: which, marks: value.marks, targets: value.targets, groups: value.groups,
      guess: new Map(value.targets.flatMap((at, index) => value.guesses && value.guess ? [[at, value.guesses[value.guess[index]]] as const] : [])) }); };
    if (!same) found = cache?.key === now ? cache.found : null;
    error = '';
    const current = ++request;
    if (which !== 'unsure' && !taxon || wait) { busy = false; onoverlay({ entry: which, marks: [], targets: [], groups: [], guess: new Map() }); return; }
    if (!same && found) show(found);
    busy = true;
    (part ? api<Found>('/explore/entries/clip', { dataset_id: datasetId, species_id: taxon, source_kind: part.source.kind, source_id: part.source.id, start_s: part.start_s, end_s: part.end_s })
      : api<Found>(`/explore/entries/${which}?dataset=${datasetId}${taxon ? `&species_id=${encodeURIComponent(taxon)}` : ''}`)).then(value => {
      if (current !== request) return;
      cache = { key: now, found: value }; show(value);
    }, e => { if (current === request) error = e instanceof Error ? e.message : 'unknown_error'; }).finally(() => { if (current === request) busy = false; });
  }); });
  function chooseReference(row: AudioSource) { source = row; start = 0; end = Math.min(5, row.seconds); }
  const addFile = createTask(async () => { let path = referencePath;
    if (isTauri()) { const chosen = await open({ multiple: false, filters: [{ name: t('referenceAudio'), extensions: ['wav', 'flac', 'mp3'] }] }); if (!chosen) return; path = chosen; }
    chooseReference(await api<AudioSource>('/references', { path }));
  }, { toast: true });
  function chooseFrom(name: From) { from = name; clip = null; source = null; if (name === 'file' && isTauri()) void addFile.run(); }
  const about = (group: Group) => [fill(`meCount_${entry}`, { n: group.targets }),
    group.left < group.targets ? fill('meChecked', { n: group.targets - group.left }) : t('meNew')].join(t('meSep'));
  // The species the AI answered most often in a group (the "likely mixed up" way in): up to three, most sounds first.
  const answered = (group: Group) => { if (!found?.guesses || !found.guess) return '';
    const counts = new Map<number, number>();
    found.targets.forEach((at, index) => { if (at >= group.lo && at < group.hi) counts.set(found!.guess![index], (counts.get(found!.guess![index]) ?? 0) + 1); });
    const names = [...counts].sort((a, b) => b[1] - a[1] || a[0] - b[0]).map(([which, n]) => fill('meGuessCount', { name: speciesName(found!.guesses![which]), n }));
    return names.length ? fill(names.length > 3 ? 'meGuessesMore' : 'meGuesses', { names: names.slice(0, 3).join(t('meGuessSep')) }) : ''; };
</script>
<section class="panel entries" aria-labelledby="entries-title">
  <div class="actions" role="group" aria-label={t('meTitle')}>{#each ['unsure', 'species', 'examples'] as const as name}
    <Button active={entry === name} aria-pressed={entry === name} onclick={() => entry = name}>{t(`meTab_${name}`)}</Button>{/each}</div>
  <h2 id="entries-title">{t(`meHead_${entry}`)}</h2>
  <p>{t(`meIntro_${entry}`)}</p>
  {#if entry !== 'unsure'}
    {#if species}<p class="chosen"><strong>{taxonName(species)}</strong> <i>{species.scientific_name}</i> <Button size="sm" onclick={() => { species = null; clip = null; source = null; }}>{t('changeSpecies')}</Button></p>
    {:else}<SpeciesChoice {datasetId} onchoose={taxon => species = taxon} />{/if}{/if}
  {#if entry === 'examples' && species}
    <div class="actions" role="group" aria-label={t('wSource')}>{#each ['heard', 'xc', 'file'] as const as name}
      <Button active={from === name} aria-pressed={from === name} pending={name === 'file' && addFile.pending} onclick={() => chooseFrom(name)}>{t(`meFrom_${name}`)}</Button>{/each}</div>
    {#if from !== 'heard'}
      {#if clip}<p class="chosen">{fill('meClipNow', { file: fileName(clip.source.path), start: +clip.start_s.toFixed(1), end: +clip.end_s.toFixed(1) })} <Button size="sm" onclick={() => clip = null}>{t('meClipChange')}</Button></p>
      {:else if source}{#key `${source.kind}:${source.id}`}<p>{fileName(source.path)} · {t('meClipPart')}</p>
        <AudioRegion {source} secondary bind:start bind:end invalid={valid ? '' : fill('uClipLength', { max: 30 })} />{/key}
        <div class="actions"><Button variant="primary" disabled={!valid} onclick={() => clip = { source: source!, start_s: start, end_s: end }}>{t('meUseClip')}</Button>
          {#if from === 'xc'}<Button onclick={() => source = null}>{t('uBackSounds')}</Button>{/if}</div>
      <!-- Without a Xeno-Canto key the search itself says so and links to Settings. -->
      {:else if from === 'xc'}{#key species.id}<XenoCantoSearch {species} onselect={chooseReference} />{/key}
      {:else if !isTauri()}<label>{t('referencePath')}<input bind:value={referencePath} /></label><Button pending={addFile.pending} disabled={!referencePath.trim()} onclick={() => addFile.run()}>{t('referenceFromFile')}</Button>{/if}
    {/if}
  {/if}
  {#if busy && !found}<p role="status"><Spinner /> {t('loading')}</p>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <!-- Targets outside every suggested group; those of the first way can be heard from the site page's list. -->
  {#snippet scattered(key: string, n: number)}<p class="muted">{fill(key, { n })}{#if entry === 'unsure'}{' '}<a href={resolve(`/datasets/${datasetId}`)}>{t('meScatteredSite')}</a>{/if}</p>{/snippet}
  {#if found}
    {#if entry === 'species'}<p class="muted">{fill('meSpTotal', { detected: found.targets.length, heard: found.marks.filter(mark => mark[1] === 1).length })}</p>
    {:else if outside}<p class="muted">{fill('meClipTotal', { n: found.targets.length })}</p>
    {:else if entry === 'examples' && !found.examples}<p class="notice">{found.mixed ? fill('meExMixed', { n: found.mixed }) : t('meExNone')}</p>
    {:else if entry === 'examples'}<p class="muted">{fill('meExTotal', { n: found.examples ?? 0 })}{#if found.mixed}{' '}{fill('meExLeftOut', { n: found.mixed })}{/if}</p>
    {:else if found.targets.length}<p class="muted">{fill('meUnTotal', { n: found.targets.length })}</p>{/if}
    {#if found.groups.length}<ol>{#each found.groups as group, index (group.node)}<li class:current={group.node === node}>
      <div><strong>{fill('meGroup', { n: 'ABCDEFGHIJ'[index], size: group.size })}</strong><span>{about(group)}</span>{#if answered(group)}<span class="muted">{answered(group)}</span>{/if}</div>
      {#if group.node === node}<span class="shown">{t('meShown')}</span>{:else}<Button size="sm" onclick={() => go(group.node)}>{t('meLook')}</Button>{/if}</li>{/each}</ol>
      <p class="muted">{t('meListen')}</p>
      {#if found.scattered}{@render scattered('meScattered', found.scattered)}{/if}
    {:else if found.targets.length}{@render scattered('meScatteredOnly', found.scattered)}
    {:else if entry !== 'examples' || found.examples || outside}<p>{t(`meNone_${entry}`)}</p>{/if}
  {/if}
</section>
<style>.entries{display:flex;flex-direction:column;gap:8px}.entries h2,.entries p{margin:0}ol{list-style:none;margin:0;padding:0}
  li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 12px;border-top:1px solid rgb(var(--stone-300))}li.current{border-left:4px solid rgb(var(--primary-700))}
  li div{display:flex;flex-direction:column}.shown{font-weight:700;white-space:nowrap}li :global(.button){flex-shrink:0;white-space:nowrap}
  .chosen{display:flex;flex-wrap:wrap;gap:8px;align-items:center}</style>
