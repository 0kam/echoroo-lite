<script lang="ts">
  import { resolve } from '$app/paths';
  import { untrack } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app, t, activeJob, datasetOf, fileName, duration, siteTime, loadRecordings, refreshCatalog, type Recording } from '$lib/app.svelte';
  import { candidateName, commonName, type NamedCandidate } from '$lib/species';
  import AudioRegion, { type PlayerStatus } from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type Candidate = NamedCandidate & { start_s: number; end_s: number; score: number; raw_score?: number };
  type Result = { id: number; job_id: number; engine: string; metadata: { score_type: string; display_geo?: string }; candidates: Candidate[] };
  const datasetId = $derived(Number(page.params.id)), recordingId = $derived(Number(page.params.rid));
  const dataset = $derived(datasetOf(datasetId));
  let records = $state<Recording[]>([]), results = $state<Result[]>([]);
  let loading = $state(true), loadingResults = $state(false), loadError = $state('');
  const index = $derived(records.findIndex(r => r.id === recordingId));
  const recording = $derived(index >= 0 ? records[index] : null);
  const previous = $derived(index > 0 ? records[index - 1] : null), next = $derived(index >= 0 ? records[index + 1] ?? null : null);
  const job = $derived(activeJob()), jobHere = $derived(job?.dataset_id === datasetId ? job : null);
  let player = $state<{ play: (from: number, to: number) => Promise<void>; stop: () => void }>(null!);
  let status = $state<PlayerStatus>('idle'), start = $state(0), end = $state(5);
  let activeRow = $state('');
  let lastJobs = '';
  $effect(() => {
    const id = datasetId, version = app.recordingsVersion;
    loading = true; loadError = '';
    loadRecordings(id).then(rows => {
      if (id === datasetId && version === app.recordingsVersion) {
        records = rows; start = 0; end = Math.min(5, rows.find(row => row.id === recordingId)?.seconds ?? 5);
      }
    })
      .catch(e => loadError = e instanceof Error ? e.message : 'unknown_error')
      .finally(() => loading = false);
  });
  async function loadResults(id: number) {
    loadingResults = true;
    try { const data = await api<Result[]>(`/recordings/${id}/results`); if (id === recordingId) results = data; }
    catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
    finally { loadingResults = false; }
  }
  // Reload results when the recording changes and whenever an analysis job changes state.
  $effect(() => { const id = recordingId; results = []; activeRow = ''; lastJobs = untrack(() => JSON.stringify(app.jobs)); void loadResults(id); });
  $effect(() => {
    const state = JSON.stringify(app.jobs);
    if (state !== lastJobs) { lastJobs = state; void loadResults(recordingId); }
  });
  const analyze = createTask(async () => {
    await api('/jobs', { dataset_id: datasetId, recording_id: recordingId });
    await refreshCatalog(); notify('analysisStarted');
  }, { toast: true });
  async function listen(result: Result, row: Candidate) {
    const key = `${result.engine}:${row.start_s}:${row.end_s}:${row.label}`;
    if (activeRow === key && status !== 'idle') { player.stop(); return; }
    activeRow = key; await player.play(row.start_s, row.end_s);
  }
  const engineName = (engine: string) => engine === 'birdnet' ? 'BirdNET 2.4' : 'Perch V2';
</script>

<div class="page">
  {#if loading}<p class="muted"><Spinner /> {t('loading')}</p>
  {:else if !recording}
    <div class="panel empty"><p>{t(loadError || 'recordingNotFound')}</p><Button href={resolve(`/datasets/${datasetId}`)}>{t('navRecordings')}</Button></div>
  {:else}
    <div class="page-header">
      <div>
        <div class="crumbs"><a href={resolve('/datasets')}>{t('navLibrary')}</a><span>›</span><a href={resolve(`/datasets/${datasetId}`)}>{dataset?.name}</a><span>›</span></div>
        <h1>{fileName(recording.path)}</h1>
        <p class="mono">{siteTime(recording)} · {recording.timezone} · {duration(recording.seconds)}</p>
      </div>
      <div class="actions">
        <Button size="sm" href={previous ? resolve(`/datasets/${datasetId}/recordings/${previous.id}`) : undefined} disabled={!previous}>‹ {t('previousRecording')}</Button>
        <Button size="sm" href={next ? resolve(`/datasets/${datasetId}/recordings/${next.id}`) : undefined} disabled={!next}>{t('nextRecording')} ›</Button>
        <Button href={resolve(`/datasets/${datasetId}/search?recording=${recordingId}`)}>{t('searchFromRecording')}</Button>
        <Button variant="primary" pending={analyze.pending} disabled={!!job} onclick={() => analyze.run()}>{t(jobHere ? 'analysisInProgress' : 'analyze')}</Button>
      </div>
    </div>
    <section class="panel">
      <h2>{t('audio')}</h2>
      {#key recording.id}<AudioRegion source={{ ...recording, kind: 'recording' }} bind:start bind:end bind:status bind:this={player} />{/key}
    </section>
    <section class="panel">
      <div class="section-heading">
        <h2>{t('candidates')}</h2>
        <span class="muted">{t('candidateRowHint')}</span>
      </div>
      {#if jobHere}<div class="jobcard"><JobStatus job={jobHere} /></div>{/if}
      {#if loadingResults && !results.length}<p class="muted"><Spinner /> {t('loading')}</p>
      {:else if !results.length}
        <div class="empty"><p>{t(jobHere ? 'analysisPending' : 'notAnalysed')}</p>
          {#if !jobHere}<Button variant="primary" pending={analyze.pending} disabled={!!job} onclick={() => analyze.run()}>{t('analyze')}</Button>{/if}</div>
      {:else}
        <details class="about"><summary>{t('aboutScores')}</summary>
          <p class="notice">{t('rawScores')}</p><p class="muted">{t('candidateLimit')}</p><p class="muted">{t('regionFilterShared')}</p></details>
        <div class="model-grid">
          {#each results as result (result.engine)}
            <div class="model">
              <h3>{engineName(result.engine)}</h3>
              <p class="muted">{t('analysisRun')} #{result.job_id} · {t(result.engine === 'birdnet' ? 'birdnetScore' : 'perchScore')} · {result.candidates.length}</p>
              {#if result.metadata.display_geo === 'unavailable'}<p class="notice">{t('perchGeoUnavailable')}</p>{/if}
              <div class="table-wrap candidate-list">
                <table>
                  <thead><tr><th>{t('species')}</th><th>{t('score')}</th><th>{t('segment')}</th></tr></thead>
                  <tbody>
                    {#each result.candidates as row}
                      {@const key = `${result.engine}:${row.start_s}:${row.end_s}:${row.label}`}
                      {@const active = activeRow === key && status !== 'idle'}
                      <tr class="row-link" class:chosen={active} onclick={() => listen(result, row)}>
                        <td title={`${t('originalLabel')}: ${row.label}`}>
                          <span class="common-name">{row.scientific_name ? commonName(row, app.locale) || t('commonNameMissing') : candidateName(row, app.locale)}</span>
                          {#if row.scientific_name}<i class="scientific-name">{row.scientific_name}</i>{:else}<span class="muted">{t('soundClass')}</span>{/if}
                        </td>
                        <td class="mono" title={row.raw_score === undefined ? undefined : `${t('rawLogit')}: ${row.raw_score}`}>{row.score.toFixed(3)}</td>
                        <td class="mono nowrap">
                          <button class="button sm" class:active aria-pressed={active} aria-label={`${t('play')} ${candidateName(row, app.locale)} ${duration(row.start_s)}`}
                            onclick={e => { e.stopPropagation(); listen(result, row); }}>
                            {#if active && status === 'loading'}<Spinner size={12} />{:else if active}❚❚{:else}▶{/if}
                            {duration(row.start_s)}–{duration(row.end_s)}
                          </button>
                        </td>
                      </tr>
                    {/each}
                  </tbody>
                </table>
              </div>
            </div>
          {/each}
        </div>
      {/if}
    </section>
  {/if}
</div>

<style>
  .model-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(360px, 1fr)); gap: 20px; }
  .model h3 { margin-bottom: 2px; }
  .model .muted { margin-bottom: 8px; }
  .candidate-list { max-height: 480px; }
  .nowrap { white-space: nowrap; }
  .about { margin-bottom: 12px; }
  .about p { margin-top: 8px; }
  .jobcard { margin-bottom: 12px; }
</style>
