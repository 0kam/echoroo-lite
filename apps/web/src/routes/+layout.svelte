<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { app, t, activeJob, datasetOf, siteOf, setLocale, setDark, rememberDataset, restorePreferences, startSession } from '$lib/app.svelte';
  import ActivityBar from '$lib/ui/ActivityBar.svelte';
  import Toasts from '$lib/ui/Toasts.svelte';
  import './app.css';
  let { children } = $props();
  const job = $derived(activeJob());
  const dataset = $derived(datasetOf(app.datasetId));
  const site = $derived(siteOf(dataset));
  const base = $derived(app.datasetId === null ? null : resolve(`/datasets/${app.datasetId}`));
  const route = $derived(page.route.id ?? '');
  const current = (prefix: string, exact = false) => exact ? route === prefix : route.startsWith(prefix);
  // Visiting a dataset route makes it the working dataset for the sidebar links.
  $effect(() => {
    const id = Number(page.params.id);
    if (route.startsWith('/datasets/[id]') && Number.isInteger(id) && id !== app.datasetId) rememberDataset(id);
  });
  onMount(() => { restorePreferences(); return startSession(); });
</script>

<svelte:head><title>Echoroo Lite</title><link rel="icon" type="image/svg+xml" href="/favicon.svg" /></svelte:head>

<ActivityBar />
<div class="shell">
  <header class="topbar">
    <a class="brand" href={resolve('/')}><img src="/echoroo.png" alt="" /> Echoroo <b>Lite</b></a>
    {#if dataset}
      <span class="context"><span>{t('currentDataset')}:</span><strong>{site?.name} › {dataset.name}</strong>
        <a href={resolve('/datasets')} class="small-text">{t('switchDataset')}</a></span>
    {/if}
    <span class="spacer"></span>
    {#if job}
      {@const target = datasetOf(job.dataset_id)}
      <a class="jobchip" href={resolve('/datasets')}><span class="dot-pulse"></span>{t('analysisInProgress')}: {target?.name ?? `#${job.dataset_id}`} · {job.done} / {job.total}</a>
    {/if}
    <span class="status" class:ok={app.connected && !app.connectionError} class:bad={!!app.connectionError}>
      <span class="dot"></span>{t(app.connectionError || (app.connected ? 'ready' : 'connecting'))}
    </span>
    <select aria-label={t('language')} value={app.locale} onchange={e => setLocale(e.currentTarget.value as 'ja' | 'en')}>
      <option value="ja">日本語</option><option value="en">English</option>
    </select>
    <button class="button ghost sm" onclick={() => setDark(!app.dark)}>{t(app.dark ? 'lightTheme' : 'darkTheme')}</button>
  </header>
  <nav class="sidenav" aria-label={t('workflow')}>
    <div class="group">{t('workflow')}</div>
    <a href={resolve('/datasets')} class:current={current('/datasets', true) || current('/datasets/new')}><span class="step" aria-hidden="true">1</span>{t('navLibrary')}</a>
    {#if base}
      <a href={base} class:current={current('/datasets/[id]', true) || current('/datasets/[id]/recordings')}><span class="step" aria-hidden="true">2</span>{t('navRecordings')}</a>
      <a href={`${base}/search`} class:current={current('/datasets/[id]/search')}><span class="step" aria-hidden="true">3</span>{t('navSearch')}</a>
      <a href={`${base}/reports`} class:current={current('/datasets/[id]/reports')}><span class="step" aria-hidden="true">4</span>{t('reports')}</a>
    {:else}
      <span class="disabled-link" title={t('navNeedsDataset')}><span class="step" aria-hidden="true">2</span>{t('navRecordings')}</span>
      <span class="disabled-link" title={t('navNeedsDataset')}><span class="step" aria-hidden="true">3</span>{t('navSearch')}</span>
      <span class="disabled-link" title={t('navNeedsDataset')}><span class="step" aria-hidden="true">4</span>{t('reports')}</span>
    {/if}
    <div class="group">{t('tools')}</div>
    <a href={resolve('/quiz')} class:current={current('/quiz')}><span class="step" aria-hidden="true">♪</span>{t('quiz')}</a>
    <a href={resolve('/settings')} class:current={current('/settings')}><span class="step" aria-hidden="true">⚙</span>{t('navSettings')}</a>
    <div class="foot">{t('preview')}</div>
  </nav>
  <main class="main">
    {#if app.connected}
      {#key page.url.hash}{@render children()}{/key}
    {:else}
      <div class="page"><div class="panel empty" role="status">
        <p class:error={!!app.connectionError}>{t(app.connectionError || 'connecting')}</p>
        {#if app.connectionError}<button class="button" onclick={() => location.reload()}>{t('retryConnection')}</button>{/if}
      </div></div>
    {/if}
  </main>
</div>
<Toasts />

<style>
  .dot-pulse { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .3; } }
</style>
