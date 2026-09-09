<script lang="ts">
  import { tick } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from './api';
  import { commonName, type Species } from './species';
  import { percent, type Cell, type Evidence, type Report } from './reports';
  import SpiralPlot from './SpiralPlot.svelte';
  import AudioRegion, { clipTime } from './AudioRegion.svelte';
  let { report, locale, t, onspecies }: { report: Report; locale: string; t: (key: string) => string;
    onspecies: (species: Species) => void } = $props();
  let plot = $state<{ images: () => Promise<{ svg: string; png: string }> }>(null!);
  let filter = $state<Cell | null>(null), evidence = $state<Evidence[] | null>(null), count = $state<number | null>(null);
  let clip = $state<Evidence | null>(null), start = $state(0), end = $state(5), playerKey = $state(0);
  let player = $state<{ play: () => Promise<void> }>(null!);
  let chosen = $state<string[]>([]), folder = $state(''), output = $state(''), busy = $state(false), error = $state('');
  let filtering = $state(false);
  let request = 0;
  const visible = $derived(evidence ?? report.evidence), total = $derived(count ?? report.evidence_count);
  const key = (hit: Evidence) => JSON.stringify([hit.recording_id, hit.start_s, hit.end_s]);
  const name = (path: string) => path.split(/[\\/]/).pop();
  async function select(cell: Cell | null) {
    const version = ++request; filter = cell; error = ''; filtering=true; evidence=[]; count=0;
    try {
      const result = await api<Report>(`/reports/${report.id}${cell ? `?cell_date=${cell.date}&hour=${cell.hour}` : ''}`);
      if (version === request) { evidence = result.evidence; count = result.evidence_count; }
    } catch (e) { if (version === request) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if(version===request) filtering=false; }
  }
  async function listen(hit: Evidence) {
    clip = hit; start = hit.start_s; end = hit.end_s; playerKey++;
    await tick(); await player.play();
  }
  async function exportFiles() {
    busy = true; error = ''; output = '';
    try {
      if (isTauri()) {
        const path = await open({ directory: true, multiple: false });
        if (!path) return;
        folder = path;
      }
      const images = report.species && report.cells.length ? await plot.images() : {};
      const audio = chosen.map(value => { const [recording_id, start_s, end_s] = JSON.parse(value); return { recording_id, start_s, end_s }; });
      output = (await api<{ path: string }>(`/reports/${report.id}/export`, { folder, ...images, audio })).path;
    } catch (e) { error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { busy = false; }
  }
</script>

<section aria-label={t('reportResult')}>
  <div class="section-heading"><h3>{t('reportResult')} #{report.id} · {t(report.kind==='search' ? 'searchReport' : 'detectionReport')}</h3>
    <button disabled={busy} onclick={exportFiles}>{t(busy ? 'exportingReport' : 'exportReport')}</button></div>
  {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} /></label>{/if}
  <p class="muted">{report.start_date}–{report.end_date} · {report.site.timezone} · {t('threshold')}: {report.threshold?.toFixed(3) ?? t('noThreshold')} · {report.window_s} {t('seconds')}</p>
  <p class="muted">{t('reportSnapshotHint')} {t('exportContents')}</p>
  {#if output}<p class="notice" role="status">{t('reportExported')}<br/><span class="path">{output}</span></p>{/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if report.missing_region?.length}<p class="notice">{t('perchGeoUnavailable')} ({report.missing_region.length})</p>{/if}
  <p class="notice">{t(report.kind==='search' ? 'searchReportCaution' : 'detectionReportCaution')}</p>
  <div class="species-list"><table><thead><tr><th>{t('species')}</th><th>{t('candidateWindows')}</th><th>{t('candidateRate')}</th><th>{t('candidateDays')}</th>
    {#if report.kind==='search'}<th>{t('confirmedEvidence')}</th>{/if}</tr></thead><tbody>
    {#each report.summaries as row}<tr class:chosen={report.species?.id===row.species.id}><td>
      {#if report.kind==='detection'}<button class="link" onclick={() => onspecies(row.species)}>{commonName(row.species, locale) || t('commonNameMissing')}<i class="scientific-name">{row.species.scientific_name}</i></button>
      {:else}{commonName(row.species, locale)}<i class="scientific-name">{row.species.scientific_name}</i>{/if}</td>
      <td>{row.candidate_windows ?? '—'} / {row.eligible_windows}</td><td>{percent(row.candidate_rate)}</td><td>{row.candidate_days ?? '—'} / {row.recorded_days}</td>
      {#if report.kind==='search'}<td>{row.confirmed_windows}</td>{/if}</tr>
    {:else}<tr><td colspan="4">{t('noReportCandidates')}</td></tr>{/each}
  </tbody></table></div>
  {#if report.species && report.cells.length}
    <SpiralPlot {report} {locale} {t} onselect={cell => select(cell)} bind:this={plot}/>
    {#if filter}<div class="selected-cell"><div class="section-heading"><h3>{filter.date} · {filter.hour}:00 · {t(`cell_${filter.status}`)}</h3><button class="small" onclick={() => select(null)}>{t('allCells')}</button></div>
      <p class="muted">{t('recordingEffort')}: {(filter.recorded_seconds/60).toFixed(2)} {t('minutes')} · {t('eligibleWindows')}: {filter.eligible_windows} · {t('candidateRate')}: {percent(filter.candidate_rate)}</p>
      {#if report.kind==='search'}<p class="muted">{t('maximumSimilarity')}: {filter.max_similarity?.toFixed(3) ?? '—'} · {t('meanSimilarity')}: {filter.mean_similarity?.toFixed(3) ?? '—'}</p>{/if}</div>{/if}
    <h3 class="evidence-title">{t('evidenceAudio')} · {visible.length} / {total}</h3><p class="muted">{t('evidenceLimit')} · {t('selectedForExport')}: {chosen.length}</p>
    {#if filtering}<p class="muted" role="status">{t('loadingEvidence')}</p>{/if}
    {#if clip}<div class="evidence-player"><p>{name(clip.path)} · {clipTime(clip.start_s)}–{clipTime(clip.end_s)}</p>{#key playerKey}
      <AudioRegion source={{id:clip.recording_id, kind:'recording', path:clip.path, seconds:clip.seconds}} {t} bind:start bind:end bind:this={player}/>{/key}</div>{/if}
    <div class="evidence-list"><table><thead><tr><th>{t('includeAudio')}</th><th>{t('recording')}</th><th>{t('score')}</th><th>{t('segment')}</th><th>{t('judgement')}</th></tr></thead><tbody>
      {#each visible as hit}<tr><td><input type="checkbox" value={key(hit)} bind:group={chosen} aria-label={`${t('includeAudio')} ${name(hit.path)} ${clipTime(hit.start_s)}`} /></td>
        <td>{name(hit.path)}<div class="muted">{new Intl.DateTimeFormat(locale,{dateStyle:'short',timeStyle:'medium',timeZone:report.site.timezone}).format(new Date(hit.local_time))}</div></td>
        <td>{hit.score.toFixed(3)}</td><td><button class="small" onclick={() => listen(hit)}>{clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td><td>{t(`verdict_${hit.status}`)}</td></tr>
      {:else}{#if !filtering}<tr><td colspan="5">{t('noCellEvidence')}</td></tr>{/if}{/each}
    </tbody></table></div>
  {:else if report.kind==='detection'}<p class="empty">{t('chooseReportSpecies')}</p>{/if}
</section>

<style>
  .species-list { max-height: 260px; overflow: auto; margin: 16px 0; }
  .evidence-list { max-height: 340px; overflow: auto; }
  .evidence-list input { width: 18px; height: 18px; }
  .evidence-list td:nth-last-child(2) { white-space: nowrap; }
  .selected-cell { padding: 14px; background: rgb(var(--stone-100)); margin-top: 14px; border-radius: 6px; }
  .evidence-title { margin-top: 18px; }
  .evidence-player { border: 1px solid rgb(var(--stone-300)); padding: 12px; margin-bottom: 14px; }
  .path { overflow-wrap: anywhere; }
</style>
