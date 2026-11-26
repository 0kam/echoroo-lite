<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { api } from './api.svelte';
  import { app, t, activeJob } from './app.svelte';
  import AudioRegion from './AudioRegion.svelte';
  import { screenName, taxonName, type Counts, type SearchExamples } from './heard';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { fill } from './workflow';
  // On a species page: how many sounds were checked by ear, a link to see and put them right, and (while the site
  // has no search for this species) a search for similar sounds from heard sounds the app chose as examples.
  // `ondone`: stay on the page that shows this box and tell it the search is made (else the results open). No `searchHref`: no link to choosing one's own example.
  let { datasetId, speciesId, timezone, hasSearch, range, searchHref, resultsHref, ondone }:
    { datasetId: number; speciesId: string; timezone: string; hasSearch: boolean; range: string; searchHref: string; resultsHref: string; ondone?: () => void } = $props();
  let counts = $state<Counts | null>(null), found = $state<SearchExamples | null>(null), left = $state<number[]>([]);
  $effect(() => { void speciesId;
    api<{ site: Counts }>(`/datasets/${datasetId}/heard?species_id=${encodeURIComponent(speciesId)}`).then(value => counts = value.site, () => {});
    api<SearchExamples>(`/datasets/${datasetId}/species/${speciesId}/heard-examples`).then(value => found = value, () => {}); });
  const examples = $derived(found?.examples.filter(item => !left.includes(item.id)) ?? []);
  const time = (value: string) => new Intl.DateTimeFormat(app.locale, { dateStyle: 'medium', timeStyle: 'medium', hourCycle: 'h23', timeZone: timezone }).format(new Date(value));
  const dates = $derived(new URLSearchParams(range));
  const search = createTask(async () => {
    await api('/search-runs', { dataset_id: datasetId, species_id: speciesId, start_date: dates.get('start_date'), end_date: dates.get('end_date'),
      clips: examples.map(item => ({ source_kind: 'recording', source_id: item.recording_id, start_s: item.start_s, end_s: item.end_s })) });
    if (ondone) ondone(); else await goto(resultsHref);
  });
  const total = $derived(counts ? counts.present + counts.absent + counts.unknown : 0);
</script>
{#if counts && total}<section class="panel heard-box" aria-labelledby="heard-box-title"><h2 id="heard-box-title">{t('hdSpTitle')}</h2>
  <p>{fill('hdSpCounts', counts)}　<a href={`${resolve(`/datasets/${datasetId}/heard`)}?species=${speciesId}${range ? `&${range}` : ''}`}>{t('hdSpAll')}</a></p>
  {#if hasSearch}<p>{t('hdExDone')}　<a href={resultsHref}>{t('sListenSearch')}</a></p>
  {:else if found && found.heard}<h3>{t('hdExTitle')}</h3>
    {#if examples.length}<p>{fill('hdExIntro', { heard: found.heard, n: examples.length })}</p>
      {#each examples as item (item.id)}<article class="example"><div class="example-head"><span>{time(item.local_time)}</span><span class="muted">{screenName(item.screen, item.extra)}</span>
        <Button size="sm" disabled={search.pending} onclick={() => left = [...left, item.id]}>{t('hdExRemove')}</Button></div>
        <AudioRegion source={item.source} fixedRange compact start={item.start_s} end={item.end_s} /></article>{/each}
      <div class="actions"><Button variant="primary" pending={search.pending} disabled={!!activeJob()} onclick={() => search.run()}>{search.pending ? t('hdExSearching') : fill('hdExSearch', { n: examples.length })}</Button>
        {#if searchHref}<Button href={searchHref} disabled={search.pending}>{t('hdExOwn')}</Button>{/if}</div>
      {#if search.error}<p class="error" role="alert">{t(search.error)}</p>{/if}
    {:else}<p>{t(found.examples.length ? 'uChooseExample' : 'hdExNone')}</p>{#if searchHref}<div class="actions"><Button href={searchHref}>{t('hdExOwn')}</Button></div>{/if}{/if}
    <details><summary>{t('hdExHow')}</summary><p class="muted">{t('hdExHowText')}</p>
      {#if found.mixed_example}<p class="muted">{fill('hdExMixed', { time: time(found.mixed_example.local_time), species: new Intl.ListFormat(app.locale).format(found.mixed_example.species.map(taxonName)) })}</p>{/if}</details>{/if}
</section>{/if}
<style>.heard-box h2{margin:0 0 8px}.heard-box h3{margin:16px 0 8px;font-size:18px}.heard-box p{margin:8px 0}.example{padding:8px 0;border-top:1px solid rgb(var(--stone-300))}
  .example-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;margin-bottom:4px}.example-head :global(.button){margin-left:auto}details summary{min-height:44px;padding:10px 0;cursor:pointer}</style>
