<script lang="ts" module>
  /** What a reviewer could listen to when answering; saved with the answer. */
  export type ExampleRef = { kind: 'named' | 'confirmed'; origin: string; id: number | string };
</script>

<script lang="ts">
  import { resolve } from '$app/paths';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { openExternal } from './files';
  import { speciesPage, type Species } from './species';
  import AudioRegion, { type AudioSource } from './AudioRegion.svelte';
  import { licenseName } from './XenoCantoSearch.svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { createTask } from './ui/task.svelte';
  type Example = { kind: ExampleRef['kind']; source: AudioSource; start_s: number; end_s: number;
    label: { origin: string; id: number | string; recordist?: string; license?: string; url?: string; local_time?: string; country?: string; quality?: string } };
  type Payload = { examples: Example[]; ebird_code: string | null; in_quiz: boolean };
  let { species, datasetId, validationId, practice, onexamples }:
    { species: Species; datasetId: number; validationId?: number; practice: string;
      onexamples?: (examples: ExampleRef[]) => void } = $props();
  let data = $state<Payload | null>(null), index = $state(0), error = $state(''), fetched = $state(false), request = 0;
  // Primitive dependencies: a refreshed page object for the same species must not reload or reset the examples.
  const speciesId = $derived(species.id);
  const scope = $derived({ dataset_id: datasetId, validation_id: validationId });
  const example = $derived(data?.examples[index]);
  const named = $derived(data?.examples.some(e => e.kind === 'named') ?? false);
  const page = $derived(speciesPage(species, data?.ebird_code ?? null, app.locale));
  function show(value: Payload) {
    data = value; index = 0;
    onexamples?.(value.examples.map(e => ({ kind: e.kind, origin: e.label.origin, id: e.label.id })));
  }
  $effect(() => {
    const version = ++request, query = Object.entries(scope).filter(([, v]) => v !== undefined).map(([k, v]) => `${k}=${v}`).join('&');
    error = ''; fetched = false;
    api<Payload>(`/species/${speciesId}/examples?${query}`).then(value => { if (version === request) show(value); })
      .catch(e => { if (version === request) error = e.message; });
  });
  const prepare = createTask(async () => { show(await api<Payload>(`/species/${speciesId}/examples/xeno-canto`, scope)); fetched = true; });
  const credit = (e: Example) => e.kind === 'confirmed' ? [t('refConfirmed'), e.label.local_time?.slice(0, 16).replace('T', ' ')]
    : [t('refNamed'), e.label.origin === 'quiz' ? t('refQuiz') : e.label.origin === 'xeno-canto' ? `XC${e.label.id}` : t('refSearchReference'),
      e.label.origin === 'xeno-canto' && e.label.country ? t(e.label.country === 'Japan' ? 'refJapan' : 'refOutsideJapan') : '',
      e.label.quality ? t('refQuality').replace('{quality}',e.label.quality) : '', e.label.recordist, e.label.license && licenseName(e.label.license)];
</script>

<section class="panel examples" aria-labelledby="examples-title">
  <h2 id="examples-title">{t('refTitle')}</h2>
  {#if error}<p class="error" role="alert">{t(error)}</p>
  {:else if !data}<p role="status"><Spinner /> {t('loading')}</p>
  {:else}
    {#if example}
      {#key `${example.source.kind}:${example.source.id}:${example.start_s}`}<AudioRegion source={example.source} start={example.start_s} end={example.end_s} fixedRange />{/key}
      <p class="muted credit">{credit(example).filter(Boolean).join(' · ')}{#if example.label.url} · <a href={example.label.url} onclick={e => { e.preventDefault(); void openExternal(example.label.url!); }}>{example.label.url.replace(/^(https?:)?\/\//, '').split('/')[0]}</a>{/if}</p>
      {#if data.examples.length > 1}<div class="actions between">
        <Button size="sm" disabled={index === 0} onclick={() => index--}>‹ {t('refPrevious')}</Button><span>{index + 1} / {data.examples.length}</span>
        <Button size="sm" disabled={index >= data.examples.length - 1} onclick={() => index++}>{t('refNext')} ›</Button></div>{/if}
    {:else}<p class="muted">{t('refNone')}</p>{/if}
    {#if !named}
      {#if fetched && !prepare.error}<p class="muted">{t('refXcNone')}</p>
      {:else}<Button variant="primary" pending={prepare.pending} onclick={() => prepare.run()}>{t('refPrepare')}</Button><p class="muted">{t('refPrepareHint')}</p>{/if}
      {#if prepare.error}<p class="error" role="alert">{t(prepare.error)}{#if prepare.error === 'xc_key_required'} · <a href={resolve('/settings')}>{t('navSettings')}</a>{/if}</p>{/if}
    {/if}
    <div class="actions">
      <Button size="sm" onclick={() => void openExternal(page.url)}>{t('refLearn').replace('{site}', page.name)}</Button>
      {#if data.in_quiz}<Button size="sm" href={practice}>{t('wPractice')}</Button>{/if}
    </div>
    {#if !data.in_quiz}<p class="muted">{t('wPracticeMissing')}</p>{/if}
  {/if}
</section>

<style>
  .examples { display: flex; flex-direction: column; gap: 8px; }
  .examples h2 { font-size: 18px; margin: 0; }
  .examples > :global(.button) { align-self: flex-start; }
  .credit { overflow-wrap: anywhere; font-size: 14px; }
  .actions :global(.button) { text-align: left; }
  .between { justify-content: space-between; align-items: center; flex-wrap: nowrap; }
  p { margin: 4px 0; }
</style>
