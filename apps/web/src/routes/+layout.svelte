<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount,tick } from 'svelte';
  import { beforeNavigate,afterNavigate } from '$app/navigation';
  import { page } from '$app/state';
  import { app, t, activeJob, datasetOf, siteOf, setLocale, rememberDataset, restorePreferences, startSession } from '$lib/app.svelte';
  import ModelChoice from '$lib/ModelChoice.svelte';
  import ActivityBar from '$lib/ui/ActivityBar.svelte';
  import Toasts from '$lib/ui/Toasts.svelte';
  import './app.css';
  let { children } = $props();
  const scrollPositions=new Map<string,number>();
  const scrollKey=()=>page.url.hash.split('?')[0];
  let stopRestore=()=>{};
  beforeNavigate(()=>{stopRestore();scrollPositions.set(scrollKey(),document.querySelector('main')?.scrollTop??0);});
  afterNavigate(async()=>{
    await tick();const main=document.querySelector('main');if(!main)return;
    const target=scrollPositions.get(scrollKey())??0;
    const restore=()=>{main.scrollTop=target;if(main.scrollHeight-main.clientHeight>=target)stopRestore();};
    // Results arrive after navigation; restore once the list has its full height.
    const observer=new MutationObserver(restore);
    const events=['wheel','touchstart','keydown','pointerdown'];
    stopRestore=()=>{observer.disconnect();events.forEach(event=>main.removeEventListener(event,stopRestore));};
    events.forEach(event=>main.addEventListener(event,stopRestore,{once:true}));
    observer.observe(main,{childList:true,subtree:true});restore();
  });
  const job = $derived(activeJob());
  const route = $derived(page.route.id ?? '');
  const current = (prefix: string, exact = false) => exact ? route === prefix : route.startsWith(prefix);
  // Visiting a dataset route makes it the working dataset for subsequent navigation.
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
    <nav class="main-nav" aria-label={t('workflow')}>
      <a href={resolve('/datasets')} class:current={current('/datasets')}>{t('navLibrary')}</a>
      <a href={resolve('/quiz')} class:current={current('/quiz')}>{t('quiz')}</a>
      <a href={resolve('/settings')} class:current={current('/settings')}>{t('navSettings')}</a>
    </nav>
    <span class="spacer"></span>
    {#if job}
      {@const target = datasetOf(job.dataset_id)}
      <a class="jobchip" href={resolve('/datasets')}><span class="dot-pulse"></span>{t('analysisInProgress')}: {siteOf(target)?.name ?? ''} · {job.done} / {job.total}</a>
    {/if}
    <select aria-label={t('language')} value={app.locale} onchange={e => setLocale(e.currentTarget.value as 'ja' | 'en')}>
      <option value="ja">日本語</option><option value="en">English</option>
    </select>

  </header>
  <main class="main">
    {#if app.connected}
      <!-- A lost connection is reported in the page, not in the top bar. -->
      {#if app.connectionError}<p class="error connection" role="alert">{t(app.connectionError)}</p>{/if}
      {#if app.ai.confirmed}
        {#key page.url.hash}{@render children()}{/key}
      {:else}
        <!-- First run: the AI choice comes before everything else; saving it sets `confirmed`. -->
        <div class="page narrow">
          <div class="page-header"><div><h1>{t('firstRunTitle')}</h1><p>{t('firstRunIntro')}</p></div></div>
          <section class="panel"><ModelChoice firstRun /></section>
        </div>
      {/if}
    {:else}
      <div class="page"><div class="panel empty" role="status">
        <p class:error={!!app.connectionError}>{t(app.connectionError || 'connecting')}</p>
        {#if app.connectionError === 'data_incompatible' && app.dataFolder}<p>{t('dataFolderPath')} <code>{app.dataFolder}</code></p>{/if}
        {#if app.connectionError}<button class="button" onclick={() => location.reload()}>{t('retryConnection')}</button>{/if}
      </div></div>
    {/if}
  </main>
</div>
<Toasts />

<style>
  .connection { max-width: 1120px; margin: 0 auto 16px; }
  .dot-pulse { width: 8px; height: 8px; border-radius: 50%; background: currentColor; animation: pulse 1.2s ease-in-out infinite; }
  @keyframes pulse { 50% { opacity: .3; } }
</style>
