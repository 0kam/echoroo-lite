<script lang="ts">
  import { untrack } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import AudioRegion from './AudioRegion.svelte';
  import { fill } from './workflow';
  // How each round of a species AI did on the test sounds: answers in recordings set aside for the species, never
  // used as material. Until enough "heard" sounds exist, it says how many more are needed.
  type Sound = { segment_id: number; recording_id: number; start_s: number; end_s: number; path: string; seconds: number; local_time: string; score: number };
  type Report = { state: 'none'; heard: number; needed: number }
    | { state: 'ready'; sounds: { heard: number; not_heard: number }; aim: number; threshold: number; recordings: string[];
        rounds: { round: number; found: number | null; false: number | null; current: boolean; made_from_test: boolean }[];
        missed: Sound[]; false: Sound[]; new: Sound[] };
  let { datasetId, speciesId, name, query, version, timezone }:
    { datasetId: number; speciesId: string; name: string; query: string; version: string; timezone?: string } = $props();
  let report = $state<Report | null>(null), request = 0;
  $effect(() => { void version; untrack(() => { const current = ++request;
    api<Report>(`/datasets/${datasetId}/species/${speciesId}/test-sounds?${query}`).then(value => { if (current === request) report = value; }, () => { /* The page works without this box. */ }); }); });
  const time = (sound: Sound) => new Intl.DateTimeFormat(app.locale, { dateStyle: 'medium', timeStyle: 'medium', hourCycle: 'h23', timeZone: timezone }).format(new Date(sound.local_time));
</script>
{#snippet list(title: string, sounds: Sound[])}{#if sounds.length}<details><summary>{fill(title, { n: sounds.length })}</summary>
  {#each sounds as sound (sound.segment_id)}<div class="sound"><span>{time(sound)} <span class="muted">{fill('tsScore', { value: sound.score.toFixed(2) })}</span></span>
    <AudioRegion source={{ id: sound.recording_id, kind: 'recording', path: sound.path, seconds: sound.seconds }} fixedRange compact start={sound.start_s} end={sound.end_s} /></div>{/each}
</details>{/if}{/snippet}
{#if report}<section class="panel tests" aria-labelledby="tests-title"><h2 id="tests-title">{t('tsTitle')}</h2>
  {#if report.state === 'none' && report.heard >= report.needed}
    <p>{t('tsPending')}</p>
  {:else if report.state === 'none'}
    <p>{fill('tsNeeded', { needed: report.needed, n: report.heard })}</p>
    <progress max={report.needed} value={Math.min(report.heard, report.needed)} aria-label={fill('tsProgress', { needed: report.needed, n: report.heard })}></progress>
    <p class="muted">{fill('tsProgress', { needed: report.needed, n: report.heard })}{t('tsWhy')}</p>
  {:else}
    <p>{fill('tsSounds', { total: report.sounds.heard + report.sounds.not_heard, name, heard: report.sounds.heard, not_heard: report.sounds.not_heard })}</p>
    {#if report.rounds.length}
      <div class="scroll"><table><thead><tr><th scope="col">{t('tsColAI')}</th><th scope="col">{fill('tsColFound', { n: report.sounds.heard })}</th><th scope="col">{fill('tsColFalse', { n: report.sounds.not_heard })}</th></tr></thead>
        <tbody>{#each report.rounds as row (row.round)}<tr class:current={row.current}><th scope="row">{fill(row.current ? 'tsRoundNow' : 'tsRound', { n: row.round })}</th>
          {#if row.made_from_test}<td colspan="2" class="muted">{t('tsMadeFromTest')}</td>
          {:else}<td>{fill('tsCount', { n: row.found ?? 0 })}</td><td>{report.sounds.not_heard ? fill('tsCount', { n: row.false ?? 0 }) : t('tsNoSounds')}</td>{/if}</tr>{/each}</tbody></table></div>
      <p>{fill('tsHowToRead', { value: report.threshold.toFixed(1) })}</p>
      <p class="muted">{fill(report.sounds.heard < report.aim ? 'tsNoteFew' : 'tsNote', { n: report.sounds.heard })}</p>
      {@render list('tsMissed', report.missed)}{@render list('tsFalse', report.false)}{@render list('tsNew', report.new)}
    {:else}<p class="muted">{t('tsNoAI')}</p>{/if}
    <details><summary>{fill('tsRecordings', { n: report.recordings.length })}</summary><ul>{#each report.recordings as file}<li>{file}</li>{/each}</ul><p class="muted">{t('tsRecordingsWhy')}</p></details>
  {/if}
</section>{/if}
<style>.tests p{margin:12px 0}progress{width:100%;max-width:320px;height:12px}.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:15px}
  th,td{padding:8px 12px;border-bottom:1px solid rgb(var(--stone-300));text-align:left;vertical-align:top;font-weight:400}thead th{font-weight:600;max-width:240px}
  tbody th{font-size:15px}tr.current th,tr.current td{font-weight:700}details summary{min-height:44px;padding:10px 0;cursor:pointer}
  .sound{display:flex;flex-direction:column;gap:6px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}</style>
