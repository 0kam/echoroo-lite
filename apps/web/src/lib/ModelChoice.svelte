<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api.svelte';
  import { app, t, type Model, type ModelSettings } from './app.svelte';
  import { fill, roughTime } from './workflow';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  /** The first run saves once with its start button; Settings saves on each change. */
  let { firstRun = false }: { firstRun?: boolean } = $props();
  let main = $state(app.ai.main), extra = $state([...app.ai.extra]);
  // Shown once a change added an AI: only that AI still has to run on recordings already analysed.
  let added = $state(false);
  const usable = $derived(app.ai.models.filter(model => model.available));
  const save = createTask(async () => {
    const before = new Set([app.ai.main, ...app.ai.extra]);
    try { app.ai = await api<ModelSettings>('/models', { main, extra }, 'PUT'); }
    catch (e) { if (!firstRun) { main = app.ai.main; extra = [...app.ai.extra]; } throw e; }
    added ||= [main, ...extra].some(id => !before.has(id));
  });
  // One AI per call, so each time shows up as soon as it is known; only speeds are taken, so a choice not saved yet stays on screen.
  let measuring = $state(''), stopped = false;
  const measure = createTask(async () => {
    for (const { id, name } of usable) {
      if (stopped) return;
      measuring = name;
      const fresh = await api<ModelSettings>('/models/benchmark', { engine: id });
      const speed = fresh.models.find(model => model.id === id), model = app.ai.models.find(model => model.id === id);
      if (speed && model) { model.seconds_per_hour = speed.seconds_per_hour; model.seconds_per_file = speed.seconds_per_file; }
      app.ai.seconds_per_job = fresh.seconds_per_job;
    }
  });
  function choose(id: string) { main = id; extra = extra.filter(item => item !== id); if (!firstRun) save.run(); }
  function toggle(id: string, on: boolean) { extra = on ? [...extra, id] : extra.filter(item => item !== id); if (!firstRun) save.run(); }
  onMount(() => { if (firstRun) measure.run(); return () => { stopped = true; }; });
</script>

{#snippet about(model: Model)}
  <span><strong>{model.name}</strong>{#if model.experimental} <span class="rare">{t('modelPreview')}</span>{/if}<br />
    <span class="muted">{t(`modelHint_${model.id}`)}</span>
    {#if model.seconds_per_hour != null}<br /><span class="muted">{fill('modelSpeed', { time: roughTime(model.seconds_per_hour) })}</span>{/if}</span>
{/snippet}

<div class="stack">
  <fieldset disabled={save.pending}>
    <legend>{t('modelsMain')}</legend>
    <p class="muted">{t('modelsMainHint')}</p>
    {#each usable.filter(model => model.main_capable) as model (model.id)}
      <label class="checkbox"><input type="radio" name="main-ai" checked={main === model.id} onchange={() => choose(model.id)} />{@render about(model)}</label>
    {/each}
  </fieldset>
  <fieldset disabled={save.pending}>
    <legend>{t('modelsExtra')}</legend>
    <p class="muted">{t('modelsExtraHint')}</p>
    {#each usable.filter(model => model.id !== main) as model (model.id)}
      <label class="checkbox"><input type="checkbox" checked={extra.includes(model.id)} onchange={e => toggle(model.id, e.currentTarget.checked)} />{@render about(model)}</label>
    {/each}
  </fieldset>
  <p class="muted">{t('modelsHidden')}</p>
  {#if measure.pending}<p class="muted" role="status">{fill('modelsMeasuring', { name: measuring })}</p>{/if}
  {#if measure.error}<p class="error" role="alert">{t(measure.error)}</p>{/if}
  {#if save.error}<p class="error" role="alert">{t(save.error)}</p>{/if}
  {#if firstRun}
    <Button variant="primary" size="lg" pending={save.pending} onclick={() => save.run()}>{t('firstRunStart')}</Button>
  {:else}
    {#if save.pending}<p class="muted" role="status">{t('modelsSaving')}</p>{:else if added}<p class="info" role="status">{t('modelsChanged')}</p>{/if}
    <Button size="sm" pending={measure.pending} onclick={() => measure.run()}>{t('modelsMeasure')}</Button>
  {/if}
</div>

<style>
  .checkbox { align-items: flex-start; margin-top: 12px; }
</style>
