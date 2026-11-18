<script lang="ts" module>
  export type MapOverlay = { marks: [number, 1 | 2][]; targets: number[]; examples: number[] };
  export const HEARD = '#1565c0', NOT_HEARD = '#e65100';
</script>
<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { taxonName, type Taxon } from './heard';
  import { familyLabel } from './rollups';
  import SpeciesChoice from './SpeciesChoice.svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { fill } from './workflow';
  // Three ways into the sound map. Each asks the app for groups of a suitable size and shows them as a short list;
  // "look here" opens the group on the map. What was heard by ear is drawn on the map whichever way is open.
  type Entry = 'unsure' | 'species' | 'examples';
  type Group = { node: number; size: number; detected?: number; heard?: number; examples?: number; near?: number;
    unsure?: number; outside?: number; low?: number; checked?: number; left?: number; kind?: 'family' | 'outside' | 'low'; family?: Taxon | null };
  type Found = { species?: Taxon; groups: Group[]; marks: [number, 1 | 2][]; targets: number[]; example_positions?: number[];
    detected?: number; heard?: number; examples?: number; unsure?: number; outside?: number; low?: number; mixed?: number };
  let { datasetId, node, parent, go, onoverlay, onspecies }:
    { datasetId: number; node: number; parent?: number; go: (node: number) => void; onoverlay: (overlay: MapOverlay) => void;
      onspecies?: (species: Taxon | null) => void } = $props();
  const key = () => `echoroo-map-entry:${datasetId}`;
  // The page is made anew for every group opened, so the chosen way and species are kept for the session.
  const kept = untrack(() => { try { return JSON.parse(sessionStorage.getItem(key()) || 'null'); } catch { return null; } }) as { entry: Entry; species: Taxon | null } | null;
  let entry = $state<Entry>(kept?.entry ?? 'unsure'), species = $state<Taxon | null>(kept?.species ?? null);
  let found = $state<Found | null>(null), error = $state(''), busy = $state(false), request = 0, version = $state(0);
  /** Read the numbers and marks again after an answer was recorded on the page. */
  export function refresh() { version++; }
  let last = '';
  $effect(() => { const which = entry, taxon = species?.id, again = version; untrack(() => {
    try { sessionStorage.setItem(key(), JSON.stringify({ entry, species })); } catch { /* Private windows may refuse storage. */ }
    onspecies?.(which === 'unsure' ? null : species);
    // A refresh keeps the list on screen while the new numbers load; another way or species starts empty.
    const same = last === `${which}:${taxon}`; last = `${which}:${taxon}`; void again;
    if (!same) found = null;
    error = '';
    if (which !== 'unsure' && !taxon) { onoverlay({ marks: [], targets: [], examples: [] }); return; }
    const current = ++request; busy = true;
    api<Found>(`/explore/entries/${which}?dataset=${datasetId}${taxon ? `&species_id=${encodeURIComponent(taxon)}` : ''}`).then(value => {
      if (current !== request) return;
      found = value; onoverlay({ marks: value.marks, targets: value.targets, examples: value.example_positions ?? [] });
    }, e => { if (current === request) error = e instanceof Error ? e.message : 'unknown_error'; }).finally(() => { if (current === request) busy = false; });
  }); });
  const about = (group: Group) => entry === 'species' ? fill(group.detected ? 'meSpGroup' : 'meSpGroupMissed', { detected: group.detected ?? 0, heard: group.heard ?? 0 })
    : entry === 'examples' ? fill('meExGroup', { total: found?.examples ?? 0, n: group.examples ?? 0 })
    : `${fill('meUnCount', { n: group.unsure ?? 0 })}${t('meSep')}${group.kind === 'family' && group.family ? fill('meUnFamily', { family: familyLabel(group.family) }) : t(group.kind === 'outside' ? 'meUnOutside' : 'meUnLow')}${t('meSep')}${group.checked ? fill('meUnChecked', { n: group.checked }) : t('meUnNew')}`;
</script>
<section class="panel entries" aria-labelledby="entries-title"><h2 id="entries-title">{t('meTitle')}</h2>
  <div class="actions" role="group" aria-label={t('meTitle')}>{#each ['unsure', 'species', 'examples'] as const as name}
    <Button active={entry === name} aria-pressed={entry === name} onclick={() => entry = name}>{t(`meTab_${name}`)}</Button>{/each}</div>
  <p>{t(`meIntro_${entry}`)}</p>
  {#if entry !== 'unsure'}
    {#if species}<p class="chosen"><strong>{taxonName(species)}</strong> <i>{species.scientific_name}</i> <Button size="sm" onclick={() => species = null}>{t('changeSpecies')}</Button></p>
    {:else}<SpeciesChoice {datasetId} onchoose={taxon => species = taxon} />{/if}{/if}
  {#if busy && !found}<p role="status"><Spinner /> {t('loading')}</p>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if found}
    {#if entry === 'species'}<p class="muted">{fill('meSpTotal', { detected: found.detected ?? 0, heard: found.heard ?? 0 })}</p>
    {:else if entry === 'examples' && !found.examples}<p class="notice">{found.mixed ? fill('meExMixed', { n: found.mixed }) : t('meExNone')}</p>
    {:else if entry === 'examples'}<p class="muted">{fill('meExTotal', { n: found.examples ?? 0 })}{#if found.mixed}{' '}{fill('meExLeftOut', { n: found.mixed })}{/if}</p>
    {:else if entry === 'unsure'}<p class="muted">{fill('meUnTotal', { n: found.unsure ?? 0, outside: found.outside ?? 0, low: found.low ?? 0 })}</p>{/if}
    {#if found.groups.length}<ol>{#each found.groups as group, index (group.node)}<li class:current={group.node === node}>
      <div><strong>{fill('meGroup', { n: 'ABCDEFGHIJ'[index], size: group.size })}</strong><span>{about(group)}</span></div>
      {#if group.node === node}<span class="shown">{t('meShown')}</span>{:else}<Button size="sm" onclick={() => go(group.node)}>{t('meLook')}</Button>{/if}</li>{/each}</ol>
      <p class="muted">{t('meListen')}</p>
    {:else if entry === 'species' || entry === 'unsure' || found.examples}<p>{t(`meNone_${entry}`)}</p>{/if}
  {/if}
  <p class="legend"><span><i class="mark" style:background={HEARD}></i>{t(entry === 'unsure' ? 'meLegendHeardAny' : 'hdV_present')}</span><span><i class="mark" style:background={NOT_HEARD}></i>{t('hdV_absent')}</span>
    <span><i class="ring"></i>{t(`meLegendTarget_${entry}`)}</span>{#if entry === 'examples'}<span><i class="star" style:color={HEARD}>★</i>{t('meLegendExample')}</span>{/if}
    {#if parent !== undefined}<Button size="sm" onclick={() => go(parent!)}>{t('meWider')}</Button>{/if}</p>
  <p class="muted">{t('meMapHint')}</p>
</section>
<style>.entries{display:flex;flex-direction:column;gap:8px}.entries h2,.entries p{margin:0}ol{list-style:none;margin:0;padding:0}
  li{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:8px 12px;border-top:1px solid rgb(var(--stone-300))}li.current{border-left:4px solid rgb(var(--primary-700))}
  li div{display:flex;flex-direction:column}.shown{font-weight:700;white-space:nowrap}li :global(.button){flex-shrink:0;white-space:nowrap}
  .chosen{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.legend{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center}.legend span{display:inline-flex;align-items:center;gap:6px}
  .legend i{display:inline-block;width:12px;height:12px;border-radius:50%}.legend .ring{border:2px solid currentColor;background:none}.legend .mark{box-sizing:border-box;border:2px solid currentColor}.legend .star{width:auto;height:auto;font-style:normal;line-height:1}</style>
