<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from '$lib/api.svelte';
  import { app, t, fileName, siteTime, refreshCatalog, rememberDataset } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type Preview = { count: number; seconds: number; first: string | null; skipped: { path: string; code: string }[] };
  let siteId = $state(''), siteName = $state(''), datasetName = $state(''), folder = $state('');
  let zone = $state('Asia/Tokyo'), latitude = $state<number | null>(null), longitude = $state<number | null>(null);
  let preview = $state<Preview | null>(null);
  const body = () => ({ folder, site_id: siteId ? Number(siteId) : null, site_name: siteName || '—',
    dataset_name: datasetName, timezone: zone, latitude: latitude ?? 0, longitude: longitude ?? 0 });
  const inspect = createTask(async () => { preview = await api<Preview>('/imports/preview', body()); });
  const register = createTask(async () => {
    const result = await api<{ dataset_id: number; count: number }>('/imports', body());
    app.recordingsVersion++;
    await refreshCatalog(); rememberDataset(result.dataset_id);
    notify('importRegistered');
    await goto(resolve(`/datasets/${result.dataset_id}`));
  });
  const browse = createTask(async () => {
    const picked = await open({ directory: true, multiple: false });
    if (picked) { folder = picked; preview = null; }
  });
</script>

<div class="page">
  <div class="page-header">
    <div><div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span></div><h1>{t('addDataset')}</h1><p>{t('importHint')}</p></div>
  </div>
  <form class="steps" onsubmit={e => { e.preventDefault(); inspect.run(); }} oninput={() => preview = null}>
    <section class="panel step-card">
      <h2>{t('importStepSite')}</h2><p class="muted">{t('importSiteHint')}</p>
      <div class="form-grid">
        <label>{t('site')}<select bind:value={siteId}><option value="">{t('newSite')}</option>{#each app.sites as site}<option value={String(site.id)}>{site.name}</option>{/each}</select></label>
        {#if !siteId}
          <label>{t('siteName')}<input bind:value={siteName} required maxlength="200" /></label>
          <label>{t('timezone')}<input bind:value={zone} required /></label>
          <label>{t('latitude')}<input type="number" step="any" min="-90" max="90" bind:value={latitude} required placeholder="35.49" /></label>
          <label>{t('longitude')}<input type="number" step="any" min="-180" max="180" bind:value={longitude} required placeholder="138.20" /></label>
        {/if}
      </div>
    </section>
    <section class="panel step-card">
      <h2>{t('importStepFolder')}</h2><p class="muted">{t('importDatasetHint')}</p>
      <div class="form-grid">
        <label>{t('datasetName')}<input bind:value={datasetName} required maxlength="200" /></label>
        <label>{t('folder')}<div class="inline"><input bind:value={folder} required />
          {#if isTauri()}<Button pending={browse.pending} onclick={() => browse.run()}>{t('browse')}</Button>{/if}</div></label>
      </div>
    </section>
    <section class="panel step-card">
      <h2>{t('importStepConfirm')}</h2><p class="muted">{t('importPreviewHint')}</p>
      <div class="actions">
        <Button type="submit" pending={inspect.pending} disabled={!app.connected}>{t('inspect')}</Button>
      </div>
      {#if inspect.error}<p class="error" role="alert">{t(inspect.error)}</p>{/if}
      {#if preview}
        <div class="preview">
          <div class="stat-row">
            <span><strong>{preview.count}</strong>{t('files')}</span>
            <span><strong>{Math.round(preview.seconds / 60)}</strong>{t('minutes')}</span>
            {#if preview.first}<span>{t('firstRecording')}: {siteTime({ recorded_at: preview.first, timezone: app.sites.find(site => String(site.id) === siteId)?.timezone ?? zone })}</span>{/if}
          </div>
          {#if preview.skipped.length}<details><summary>{t('skipped')} ({preview.skipped.length})</summary>{#each preview.skipped as item}<p class="muted">{fileName(item.path)} — {t(item.code)}</p>{/each}</details>{/if}
          <div class="actions">
            <Button variant="primary" size="lg" pending={register.pending} disabled={!preview.count} onclick={() => register.run()}>{t('import')}</Button>
            <span class="muted">{t('importNext')}</span>
          </div>
          {#if register.error}<p class="error" role="alert">{t(register.error)}</p>{/if}
        </div>
      {/if}
    </section>
  </form>
</div>

<style>
  .preview { display: flex; flex-direction: column; gap: 12px; background: rgb(var(--color-success-light)); padding: 14px; margin-top: 14px; border-radius: 6px; }
  .inline :global(.button) { margin-top: 5px; }
</style>
