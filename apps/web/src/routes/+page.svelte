<script lang="ts">
  import { onMount } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api, connect } from '$lib/api';
  import { en, ja, type Key } from '$lib/i18n';
  import { candidateName, commonName, type NamedCandidate } from '$lib/species';
  import SpeciesNames from '$lib/SpeciesNames.svelte';
  import AudioWorkbench from '$lib/AudioWorkbench.svelte';
  import ReportsPanel from '$lib/ReportsPanel.svelte';
  import QuizPanel from '$lib/QuizPanel.svelte';
  import './app.css';

  type Site = { id: number; name: string; timezone: string; latitude: number; longitude: number };
  type Dataset = { id: number; name: string; site_id: number };
  type Recording = { id: number; path: string; original_time: string; recorded_at: string; timezone: string; seconds: number };
  type Job = { id: number; status: string; stage: string; done: number; total: number; error: string | null };
  type Candidate = NamedCandidate & { start_s: number; end_s: number; score: number; raw_score?: number };
  type Result = { id: number; job_id: number; engine: string;
    metadata: { score_type: string; display_geo?: string }; candidates: Candidate[] };
  type Preview = { count: number; seconds: number; first: string | null; skipped: { path: string; code: string }[] };
  let locale = $state<'ja' | 'en'>('en');
  let dark = $state(false), showNames = $state(false);
  let workspaceView = $state<'recordings' | 'reports' | 'quiz'>('recordings');
  const t = (key: string) => (locale === 'ja' ? ja[key as Key] : en[key as Key]) || en[key as Key] || (locale === 'ja' ? ja.unknown_error : en.unknown_error);
  let connected = $state(false), busy = $state(false), error = $state('');
  let sites = $state<Site[]>([]), datasets = $state<Dataset[]>([]), jobs = $state<Job[]>([]);
  let records = $state<Recording[]>([]), results = $state<Result[]>([]);
  let recordingDate = $state(''), recordingPage = $state(0);
  const filteredRecords = $derived(records.filter(record => !recordingDate || record.original_time.startsWith(recordingDate)));
  const pageRecords = $derived(filteredRecords.slice(recordingPage * 100, (recordingPage + 1) * 100));
  let selectedDataset = $state<number | null>(null), selected = $state<Recording | null>(null);
  let showImport = $state(true), preview = $state<Preview | null>(null);
  let siteId = $state(''), siteName = $state(''), datasetName = $state(''), folder = $state('');
  let zone = $state('Asia/Tokyo'), latitude = $state(0), longitude = $state(0);
  let workbench = $state<{ playRecording: (start: number, end: number) => Promise<void> }>(null!);
  let lastJobState = '';
  let selectionVersion = 0;
  const active = $derived(jobs.some(j => ['queued', 'running'].includes(j.status)));
  const duration = (seconds: number) => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  const name = (path: string) => path.split(/[\\/]/).pop();
  const date = (record: Recording) => new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium', timeStyle: 'short', timeZone: record.timezone,
  }).format(new Date(record.recorded_at));
  const body = () => ({ folder, site_id: siteId ? Number(siteId) : null, site_name: siteName || '—',
    dataset_name: datasetName, timezone: zone, latitude, longitude });

  async function action(fn: () => Promise<void>) {
    busy = true; error = '';
    try { await fn(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
  async function refresh() {
    const data = await api<{ sites: Site[]; datasets: Dataset[]; jobs: Job[] }>('/catalog');
    sites = data.sites; datasets = data.datasets; jobs = data.jobs;
    const state = JSON.stringify(jobs);
    if (selected && state !== lastJobState) {
      await loadResults(selected.id);
    }
    lastJobState = state;
  }
  async function chooseDataset(id: number) {
    const version = ++selectionVersion;
    selectedDataset = id; selected = null; results = []; records = [];
    recordingDate = ''; recordingPage = 0;
    const data = await api<Recording[]>(`/datasets/${id}/recordings`);
    if (version !== selectionVersion) return;
    records = data;
    if (records.length) await chooseRecording(records[0]);
  }
  async function chooseRecording(record: Recording) {
    selected = record; results = [];
    await loadResults(record.id);
  }
  async function loadResults(id: number) {
    const data = await api<Result[]>(`/recordings/${id}/results`);
    if (selected?.id === id) results = data;
  }
  async function browse() {
    const picked = await open({ directory: true, multiple: false });
    if (picked) { folder = picked; preview = null; }
  }
  async function register() {
    const result = await api<{ dataset_id: number }>('/imports', body());
    preview = null; showImport = false;
    await refresh(); await chooseDataset(result.dataset_id);
  }
  async function analyze(all = false) {
    await api('/jobs', { dataset_id: selectedDataset, recording_id: all ? null : selected?.id });
    await refresh();
  }
  async function play(row: Candidate) {
    await workbench.playRecording(row.start_s, row.end_s);
  }
  function setLocale() {
    localStorage.setItem('echoroo-locale', locale); document.documentElement.lang = locale;
  }
  function toggleTheme() {
    dark = !dark; document.documentElement.classList.toggle('dark', dark);
    localStorage.setItem('echoroo-theme', dark ? 'dark' : 'light');
  }
  async function refreshNames() {
    if (selected) await chooseRecording(selected);
  }
  onMount(() => {
    const saved = localStorage.getItem('echoroo-locale');
    locale = saved === 'ja' || saved === 'en' ? saved : navigator.language.startsWith('ja') ? 'ja' : 'en';
    document.documentElement.lang = locale;
    dark = localStorage.getItem('echoroo-theme') === 'dark';
    document.documentElement.classList.toggle('dark', dark);
    void action(async () => {
      await connect(); connected = true; await refresh();
      if (datasets.length) { showImport = false; await chooseDataset(datasets[0].id); }
    });
    let polling = false;
    const timer = setInterval(async () => {
      if (!connected || polling) return;
      polling = true;
      try { await refresh(); } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
      finally { polling = false; }
    }, 2000);
    return () => clearInterval(timer);
  });
</script>

<svelte:head><title>Echoroo Lite</title><link rel="icon" type="image/svg+xml" href="/favicon.svg" /></svelte:head>

<header>
  <div class="brand"><img src="/echoroo.png" alt="" /> Echoroo <b>Lite</b></div>
  <span class="tag">{t('preview')}</span>
  <button class="header-action" class:chosen={showNames} aria-expanded={showNames} disabled={!connected}
    onclick={() => { showNames = !showNames; if (showNames) window.scrollTo({ top: 0 }); }}>{t('speciesNames')}</button>
  <label class="language">{t('language')}
    <select bind:value={locale} onchange={setLocale}><option value="ja">日本語</option><option value="en">English</option></select>
  </label>
  <button class="header-action" onclick={toggleTheme}>{t(dark ? 'lightTheme' : 'darkTheme')}</button>
</header>
<main>
  <div class="workspace">
    <aside>
      <div class="section-heading"><h2>{t('library')}</h2><button class="small" onclick={() => showImport = !showImport}>＋ {t('add')}</button></div>
      {#each sites as site}
        <h3>{site.name}</h3><p class="muted">{site.timezone}</p>
        {#each datasets.filter(d => d.site_id === site.id) as dataset}
          <button class:chosen={selectedDataset === dataset.id} class="dataset" onclick={() => action(() => chooseDataset(dataset.id))}>{dataset.name}</button>
        {/each}
      {/each}
      {#if !datasets.length}<p class="empty">{t('noData')}</p>{/if}
      {#if jobs.length}
        <div class="jobs"><h3>{t('jobs')}</h3>
          {#each jobs.slice(0, 3) as job}
            <div class="job"><strong>#{job.id} · {t(job.status)}</strong><p>{t(job.stage)} · {job.done} / {job.total}</p>
              <progress value={job.done} max={job.total}></progress>
              {#if job.error}<p class="error">{t(job.error)}</p>{/if}
            </div>
          {/each}
          {#if active}<button onclick={() => action(async () => { await api('/jobs/cancel', {}); })}>{t('cancel')}</button>{/if}
        </div>
      {/if}
    </aside>
    <section class="content">
      <div class="intro"><div><h1>{t('title')}</h1><p>{t('subtitle')}</p></div>
        <span class="connection">{t(connected ? 'ready' : 'connecting')}</span></div>
      {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
      <nav class="view-nav" aria-label={t('workspaceView')}>
        <button class:chosen={workspaceView==='recordings'} aria-pressed={workspaceView==='recordings'} onclick={() => workspaceView='recordings'}>{t('recordingsAndSearch')}</button>
        <button class:chosen={workspaceView==='reports'} aria-pressed={workspaceView==='reports'} disabled={!selectedDataset} onclick={() => workspaceView='reports'}>{t('reports')}</button>
        <button class:chosen={workspaceView==='quiz'} aria-pressed={workspaceView==='quiz'} disabled={!connected} onclick={() => workspaceView='quiz'}>{t('quiz')}</button>
      </nav>
      {#if showNames}<SpeciesNames {locale} {t} onupdate={refreshNames} />{/if}
      {#if showImport && workspaceView==='recordings'}
        <form class="panel import" onsubmit={(event) => { event.preventDefault(); action(async () => { preview = await api<Preview>('/imports/preview', body()); }); }} oninput={() => preview = null}>
          <h2>{t('add')}</h2>
          <div class="form-grid">
            <label>{t('site')}<select bind:value={siteId}><option value="">{t('newSite')}</option>{#each sites as site}<option value={String(site.id)}>{site.name}</option>{/each}</select></label>
            {#if !siteId}<label>{t('siteName')}<input bind:value={siteName} required maxlength="200" /></label>{/if}
            <label>{t('datasetName')}<input bind:value={datasetName} required maxlength="200" /></label>
            {#if !siteId}
              <label>{t('timezone')}<input bind:value={zone} required /></label>
              <label>{t('latitude')}<input type="number" step="any" min="-90" max="90" bind:value={latitude} required /></label>
              <label>{t('longitude')}<input type="number" step="any" min="-180" max="180" bind:value={longitude} required /></label>
            {/if}
          </div>
          <label>{t('folder')}<div class="inline"><input bind:value={folder} required />{#if isTauri()}<button type="button" onclick={() => action(browse)}>{t('browse')}</button>{/if}</div></label>
          <p class="muted">{t('importHint')}</p>
          <button type="submit" disabled={busy || !connected}>{t('inspect')}</button>
          {#if preview}
            <div class="preview"><strong>{preview.count} {t('files')} · {Math.round(preview.seconds / 60)} {t('minutes')}</strong>
              {#if preview.skipped.length}<details><summary>{t('skipped')} ({preview.skipped.length})</summary>{#each preview.skipped as item}<p>{name(item.path)} — {t(item.code)}</p>{/each}</details>{/if}
              <button class="primary" type="button" disabled={!preview.count || busy} onclick={() => action(register)}>{t('import')}</button>
            </div>
          {/if}
        </form>
      {/if}
      {#if workspaceView==='quiz'}
        <QuizPanel recording={selected} {locale} {t}/>
      {:else if workspaceView==='reports' && selectedDataset}
        {#key selectedDataset}<ReportsPanel datasetId={selectedDataset} {locale} {t}/>{/key}
      {:else}
      {#if selectedDataset}
        <div class="panel">
          <div class="section-heading"><h2>{datasets.find(d => d.id === selectedDataset)?.name}</h2><button disabled={busy || active} onclick={() => action(() => analyze(true))}>{t('analyzeAll')}</button></div>
          <p class="muted">{records.length} {t('files')} · {t('localTime')}</p>
          {#if records.length>100}<div class="recording-navigation">
            <label>{t('recordingDate')}<input type="date" bind:value={recordingDate} onchange={() => recordingPage=0}/></label>
            <button class="small" disabled={!recordingDate} onclick={() => {recordingDate='';recordingPage=0;}}>{t('clearRecordingDate')}</button>
            <span role="status">{filteredRecords.length} {t('files')} · {t('recordingPage')} {recordingPage+1} / {Math.max(1,Math.ceil(filteredRecords.length/100))}</span>
            <button class="small" disabled={recordingPage===0} onclick={() => recordingPage--}>{t('previousRecordingPage')}</button>
            <button class="small" disabled={(recordingPage+1)*100>=filteredRecords.length} onclick={() => recordingPage++}>{t('nextRecordingPage')}</button>
          </div>{/if}
          <div class="recordings"><table><thead><tr><th>{t('recording')}</th><th>{t('time')}</th><th>{t('duration')}</th></tr></thead><tbody>
            {#each pageRecords as record}<tr class:chosen={selected?.id === record.id}><td><button class="link" onclick={() => action(() => chooseRecording(record))}>{name(record.path)}</button></td><td>{date(record)}</td><td>{duration(record.seconds)}</td></tr>{/each}
          </tbody></table></div>
        </div>
      {/if}
      {#if selected}
        <div class="panel">
          <div class="section-heading"><h2>{t('audio')}</h2><button class="primary" disabled={busy || active} onclick={() => action(() => analyze())}>{t('analyze')}</button></div>
          <p>{name(selected.path)} <span class="muted">· {selected.timezone}</span></p>
          {#key selected.id}<AudioWorkbench recording={selected} datasetId={selectedDataset!} {active} {locale} {t} bind:this={workbench} />{/key}
          <h2>{t('candidates')}</h2><p class="notice">{t('rawScores')}</p><p class="muted">{t('candidateLimit')}</p>
          {#if results.length}<p class="muted">{t('regionFilterShared')}</p>{/if}
          {#if !results.length}<p class="empty">{t('noResults')}</p>{/if}
          <div class="model-grid">{#each results as result}<div><h3>{result.engine === 'birdnet' ? 'BirdNET 2.4' : 'Perch V2'}</h3>
            <p class="muted">{t('analysisRun')} #{result.job_id} · {t(result.engine === 'birdnet' ? 'birdnetScore' : 'perchScore')}</p>
            {#if result.metadata.display_geo === 'unavailable'}<p class="notice">{t('perchGeoUnavailable')}</p>{/if}
            <div class="candidate-list"><table><thead><tr><th>{t('species')}</th><th>{t('score')}</th><th>{t('segment')}</th></tr></thead><tbody>
              {#each result.candidates as row}<tr><td title={`${t('originalLabel')}: ${row.label}`}>
                <span class="common-name">{row.scientific_name ? commonName(row, locale) || t('commonNameMissing') : candidateName(row, locale)}</span>
                {#if row.scientific_name}<i class="scientific-name">{row.scientific_name}</i>{:else}<span class="muted">{t('soundClass')}</span>{/if}
              </td><td title={row.raw_score === undefined ? undefined : `${t('rawLogit')}: ${row.raw_score}`}>{row.score.toFixed(3)}</td><td><button class="small" onclick={() => action(() => play(row))} aria-label={`${t('play')} ${candidateName(row, locale)} ${row.start_s}`}>{duration(row.start_s)}–{duration(row.end_s)}</button></td></tr>{/each}
            </tbody></table></div></div>{/each}</div>
        </div>
      {:else if !showImport}<div class="panel empty">{t('selectRecording')}</div>{/if}
      {/if}
    </section>
  </div>
</main>

<style>
  .view-nav { display: flex; gap: 8px; margin-bottom: 20px; }
  .recording-navigation { display:flex; flex-wrap:wrap; align-items:end; gap:12px; margin-bottom:12px; }
</style>
