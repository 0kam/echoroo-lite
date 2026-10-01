<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, activeJob, datasetOf, siteOf, fileName, duration, siteTime, loadRecordings, refreshCatalog, type Recording } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { fill } from '$lib/workflow';
  const id = $derived(Number(page.params.id));
  const dataset = $derived(datasetOf(id)), site = $derived(siteOf(dataset));
  const job = $derived(activeJob());
  const jobHere = $derived(job?.dataset_id === id ? job : null);
  const history = $derived(app.jobs.filter(j => j.dataset_id === id && j !== jobHere).slice(0, 3));
  let records = $state<Recording[]>([]), loading = $state(true), loadError = $state('');
  let recordingDate = $state(''), pageIndex = $state(0);
  const PAGE = 100;
  const filtered = $derived(records.filter(r => !recordingDate || r.original_time.startsWith(recordingDate)));
  const pages = $derived(Math.max(1, Math.ceil(filtered.length / PAGE)));
  const visible = $derived(filtered.slice(pageIndex * PAGE, (pageIndex + 1) * PAGE));
  const totalSeconds = $derived(records.reduce((sum, r) => sum + r.seconds, 0));
  $effect(() => {
    const dataset = id, version = app.recordingsVersion;
    loading = true; loadError = '';
    loadRecordings(dataset).then(rows => { if (dataset === id && version === app.recordingsVersion) { records = rows; pageIndex = 0; } })
      .catch(e => loadError = e instanceof Error ? e.message : 'unknown_error')
      .finally(() => loading = false);
  });
  const openRecording = (record: Recording) => goto(resolve(`/datasets/${id}/recordings/${record.id}`));
</script>

<div class="page">
  {#if app.connected && !dataset}
    <div class="panel empty"><p>{t('datasetNotFound')}</p><Button href={resolve('/datasets')}>{t('navLibrary')}</Button></div>
  {:else if dataset}
    <div class="page-header">
      <div>
        <div class="crumbs"><a href={resolve(`/datasets/${id}`)}>{t('uBackResults')}</a></div>
        <h1>{site?.name} · {t('wViewRecordings')}</h1>
      </div>

    </div>
    <div>
      <section class="panel">
        <div class="section-heading">
          <h2>{t('datasetRecordings')}</h2>
          <div class="stat-row">
            <span>{fill('rRecordingsCount',{n:records.length})}</span>
            <span>{fill('wTotalLength',{duration:duration(totalSeconds)})}</span>
            <span>{t('localTime')}</span>
          </div>
        </div>
        {#if loading}<p class="muted"><Spinner /> {t('loading')}</p>
        {:else if loadError}<p class="error" role="alert">{t(loadError)}</p>
        {:else}
          {#if records.length > 48}
            <div class="filter">
              <label>{t('recordingDate')}<input type="date" bind:value={recordingDate} onchange={() => pageIndex = 0} /></label>
              <Button size="sm" disabled={!recordingDate} onclick={() => { recordingDate = ''; pageIndex = 0; }}>{t('clearRecordingDate')}</Button>
              {#if pages > 1}
                <span class="muted" role="status">{fill('rRecordingsCount',{n:filtered.length})} · {t('recordingPage')} {pageIndex + 1} / {pages}</span>
                <Button size="sm" disabled={pageIndex === 0} onclick={() => pageIndex--}>{t('previousRecordingPage')}</Button>
                <Button size="sm" disabled={pageIndex + 1 >= pages} onclick={() => pageIndex++}>{t('nextRecordingPage')}</Button>
              {/if}
            </div>
          {/if}
          <div class="table-wrap recordings">
            <table aria-label={t('datasetRecordings')}>
              <thead><tr><th>{t('recording')}</th><th>{t('time')}</th><th>{t('duration')}</th><th></th></tr></thead>
              <tbody>
                {#each visible as record (record.id)}
                  <tr class="row-link" onclick={() => openRecording(record)}>
                    <td><a href={resolve(`/datasets/${id}/recordings/${record.id}`)} onclick={e => e.stopPropagation()}>{fileName(record.path)}</a></td>
                    <td class="mono">{siteTime(record)}</td><td class="mono">{duration(record.seconds)}</td>
                    <td class="right"><span class="button sm ghost">{t('play')} ›</span></td>
                  </tr>
                {:else}<tr><td colspan="4" class="muted">{t(recordingDate ? 'noRecordingsForDate' : 'noData')}</td></tr>{/each}
              </tbody>
            </table>
          </div>
        {/if}
      </section>

    </div>
  {/if}
</div>

<style>
  .filter { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 10px; margin-bottom: 12px; }
  .filter label { width: 180px; }
  .recordings { max-height: calc(100vh - 300px); }
  .recordings a { color: inherit; text-decoration: none; font-weight: 500; }
  .right { text-align: right; }
</style>
