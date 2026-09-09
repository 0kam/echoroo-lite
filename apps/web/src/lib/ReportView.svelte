<script lang="ts">
  import { tick } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { percent, type Cell, type Evidence, type Report } from './reports';
  import SpiralPlot from './SpiralPlot.svelte';
  import AudioRegion, { clipTime, type PlayerStatus } from './AudioRegion.svelte';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  let { report, liveUrl = '' }: { liveUrl?: string; report: Report } = $props();
  let filter = $state<Cell | null>(null), evidence = $state<Evidence[] | null>(null), count = $state<number | null>(null);
  let clip = $state<Evidence | null>(null), start = $state(0), end = $state(5), playerKey = $state(0);
  let player = $state<{ play: () => Promise<void>; stop: () => void }>(null!);
  let status = $state<PlayerStatus>('idle');
  let error = $state('');
  let filtering = $state(false);
  let request = 0;
  const visible = $derived(evidence ?? report.evidence), total = $derived(count ?? report.evidence_count);
  const key = (hit: Evidence) => JSON.stringify([hit.recording_id, hit.start_s, hit.end_s]);
  const name = (path: string) => path.split(/[\\/]/).pop();
  async function select(cell: Cell | null) {
    const version = ++request; filter = cell; error = ''; filtering=true; evidence=[]; count=0;
    try {
      const response = await api<Report | {report:Report}>(liveUrl ? `${liveUrl}${cell?`&cell_date=${cell.date}&hour=${cell.hour}`:''}` : `/reports/${report.id}${cell ? `?cell_date=${cell.date}&hour=${cell.hour}` : ''}`);
      const result = 'report' in response ? response.report : response;
      if (version === request) { evidence = result.evidence; count = result.evidence_count; }
    } catch (e) { if (version === request) error = e instanceof Error ? e.message : 'unknown_error'; }
    finally { if(version===request) filtering=false; }
  }
  async function listen(hit: Evidence) {
    if (clip && key(clip) === key(hit) && status !== 'idle') { player.stop(); return; }
    clip = hit; start = hit.start_s; end = hit.end_s; playerKey++;
    await tick(); await player.play();
  }
</script>

<section aria-label={t('reportResult')}>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if report.missing_region?.length}<p class="notice">{t('perchGeoUnavailable')} ({report.missing_region.length})</p>{/if}
  {#if report.species && report.cells.length}
    <SpiralPlot {report} onselect={cell => select(cell)}/>
    {#if filter}<div class="selected-cell"><div class="section-heading"><h3>{filter.date} · {filter.hour}:00 · {t(`cell_${filter.status}`)}</h3><Button size="sm" onclick={() => select(null)}>{t('allCells')}</Button></div>
      <p class="muted">{t('recordingEffort')}: {(filter.recorded_seconds/60).toFixed(2)} {t('minutes')} · {t('eligibleWindows')}: {filter.eligible_windows} · {t('candidateRate')}: {percent(filter.candidate_rate)}</p>
      {#if report.kind==='search'}<p class="muted">{t('maximumSimilarity')}: {filter.max_similarity?.toFixed(3) ?? '—'} · {t('meanSimilarity')}: {filter.mean_similarity?.toFixed(3) ?? '—'}</p>{/if}</div>{/if}
    <h3 class="evidence-title">{t('evidenceAudio')} · {visible.length} / {total}</h3><p class="muted">{t('evidenceLimit')}</p>
    {#if filtering}<p class="muted" role="status"><Spinner size={12} /> {t('loadingEvidence')}</p>{/if}
    {#if clip}<div class="evidence-player"><p>{name(clip.path)} · {clipTime(clip.start_s)}–{clipTime(clip.end_s)}</p>{#key playerKey}
      <AudioRegion source={{id:clip.recording_id, kind:'recording', path:clip.path, seconds:clip.seconds}} bind:start bind:end bind:status bind:this={player}/>{/key}</div>{/if}
    <div class="evidence-list"><table><thead><tr><th>{t('recording')}</th><th>{t('score')}</th><th>{t('segment')}</th><th>{t('judgement')}</th></tr></thead><tbody>
      {#each visible as hit}<tr>
        <td>{name(hit.path)}<div class="muted">{new Intl.DateTimeFormat(app.locale,{dateStyle:'short',timeStyle:'medium',timeZone:report.site.timezone}).format(new Date(hit.local_time))}</div></td>
        <td>{hit.score.toFixed(3)}</td><td><button class="button sm" class:active={!!clip && key(clip) === key(hit) && status !== 'idle'} aria-pressed={!!clip && key(clip) === key(hit) && status !== 'idle'} onclick={() => listen(hit)}>
          {#if clip && key(clip) === key(hit) && status === 'loading'}<Spinner size={12} />{:else if clip && key(clip) === key(hit) && status !== 'idle'}❚❚{:else}▶{/if} {clipTime(hit.start_s)}–{clipTime(hit.end_s)}</button></td><td>{t(`verdict_${hit.status}`)}</td></tr>
      {:else}{#if !filtering}<tr><td colspan="5">{t('noCellEvidence')}</td></tr>{/if}{/each}
    </tbody></table></div>
  {/if}
</section>

<style>
  .evidence-list { max-height: 340px; overflow: auto; }
  .evidence-list td:nth-last-child(2) { white-space: nowrap; }
  .selected-cell { padding: 14px; background: rgb(var(--stone-100)); margin-top: 14px; border-radius: 6px; }
  .evidence-title { margin-top: 18px; }
  .evidence-player { border: 1px solid rgb(var(--stone-300)); padding: 12px; margin-bottom: 14px; }
</style>
