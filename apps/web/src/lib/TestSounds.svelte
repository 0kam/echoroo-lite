<script lang="ts">
  import { tick, untrack } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import AudioRegion from './AudioRegion.svelte';
  import Button from './ui/Button.svelte';
  import { checkProgress, checkText, fill, share, type Validation } from './workflow';
  // How each round of a species AI did on the test sounds: answers in recordings set aside for the species, never
  // used as material. Two numbers to compare the rounds by; the rest is folded. A species grown without test sounds
  // only gets a line saying so. The third number is the species AI's own check (step 3 of its page), as that check
  // worked it out: shown for the current AI only, since making the AI again outdates it.
  type Sound = { segment_id: number; recording_id: number; start_s: number; end_s: number; path: string; seconds: number; local_time: string; score: number };
  type Round = { round: number; found: number | null; false: number | null; current: boolean; made_from_test: boolean };
  // `fixed`: the set is closed to later answers (not for a species whose test sounds were frozen by an earlier version).
  type Report = { state: 'none' }
    | { state: 'ready'; sounds: { heard: number; not_heard: number }; fixed: boolean; threshold: number; recordings: string[]; rounds: Round[];
        missed: Sound[]; false: Sound[]; new: Sound[] };
  let { datasetId, speciesId, query, version, timezone, check = null, oncontinue, reveal = false }:
    { datasetId: number; speciesId: string; query: string; version: string; timezone?: string; check?: Validation | null; oncontinue?: () => void; reveal?: boolean } = $props();
  const precision = $derived(check?.status === 'decided' ? check.results[check.engine]?.precision : null);
  let report = $state<Report | null>(null), request = 0;
  $effect(() => { void version; untrack(() => { const current = ++request;
    api<Report>(`/datasets/${datasetId}/species/${speciesId}/test-sounds?${query}`).then(async value => { if (current !== request) return; const first = !report; report = value;
      // `reveal`: the page was come back to for these scores, so bring them into view once they are there.
      if (first && reveal) { await tick(); document.getElementById('tests-title')?.scrollIntoView({ block: 'start' }); } }, () => { /* The page works without this box. */ }); }); });
  const time = (sound: Sound) => new Intl.DateTimeFormat(app.locale, { dateStyle: 'medium', timeStyle: 'medium', hourCycle: 'h23', timeZone: timezone }).format(new Date(sound.local_time));
</script>
{#snippet list(title: string, sounds: Sound[])}{#if sounds.length}<details><summary>{fill(title, { n: sounds.length })}</summary>
  {#each sounds as sound (sound.segment_id)}<div class="sound"><span>{time(sound)} <span class="muted">{fill('tsScore', { value: sound.score.toFixed(2) })}</span></span>
    <AudioRegion source={{ id: sound.recording_id, kind: 'recording', path: sound.path, seconds: sound.seconds }} fixedRange compact start={sound.start_s} end={sound.end_s} /></div>{/each}
</details>{/if}{/snippet}
<!-- One number: its share of `of` sounds, the change from the round before (`up`: whether more is better), and a bar per
     scored round. The bars are decoration: the folded table holds the same numbers. -->
{#snippet card(label: string, none: string, of: number, value: (row: Round) => number, up: boolean, scored: Round[])}
  {@const at = scored.findIndex(row => row.current)}{@const now = value(scored[at])}{@const change = at ? now - value(scored[at - 1]) : null}
  <div class="score"><div class="muted">{t(label)}</div>
    {#if of}<div class="big">{Math.round(100 * now / of)}%</div><div>{fill('tsOf', { n: of, m: now })}</div>
      {#if change != null}<div class="change" class:good={change !== 0 && change > 0 === up} class:bad={change !== 0 && change > 0 !== up}>
        {change ? fill(change > 0 ? 'tsMore' : 'tsFewer', { k: Math.abs(change) }) + t(change > 0 === up ? 'tsGood' : 'tsBad') : t('tsSame')}</div>{/if}
      <div class="bars" aria-hidden="true">{#each scored as row (row.round)}<span class:now={row.current} style:height="{Math.max(6, 100 * value(row) / of)}%"><i>{row.round}</i></span>{/each}</div>
    {:else}<div class="big">—</div><div class="muted">{t(none)}</div>{/if}
  </div>
{/snippet}
{#snippet measured()}<div class="score"><div class="muted">{t('s3Label')}</div>
  {#if precision}<div class="big">{fill('s3Value', { p: share(precision.value) })}</div>
    <div>{fill('s3Of', { n: check!.counts.answered, low: share(precision.low), high: share(precision.high) })}</div>
  {:else}<div class="big">—</div>
    {#if check?.status === 'in_progress' && check.counts.answered}<div class="muted">{fill('s3Progress', { answered: check.counts.answered, total: checkProgress(check).goal })}</div>
      <Button onclick={oncontinue}>{t('s3Continue')}</Button>
    {:else}<div class="muted">{check?.status === 'stale' ? t('s3Stale') : check?.stage === 'complete' ? checkText(check) : t('s3None')}</div>{/if}{/if}
</div>{/snippet}
{#if report}<section class="panel tests" aria-labelledby="tests-title"><h2 id="tests-title">{t('tsTitle')}</h2>
  {#if report.state === 'none'}
    <p>{t('tsNone')}</p>
    <div class="scores">{@render measured()}</div>
  {:else}{@const sounds = report.sounds}{@const scored = report.rounds.filter(row => !row.made_from_test)}
    {#if scored.some(row => row.current)}
      <div class="scores">{@render card('tsFoundLabel', 'tsNoHeard', sounds.heard, row => row.found ?? 0, true, scored)}
        {@render card('tsFalseLabel', 'tsNoNotHeard', sounds.not_heard, row => row.false ?? 0, false, scored)}{@render measured()}</div>
      <p>{t('tsBetter')}{#if precision} {t('s3Report')}{/if}</p>
      <p class="muted">{fill('tsCompare', { n: sounds.heard + sounds.not_heard })}</p>
    {:else}<p class="muted">{t(report.rounds.length ? 'tsMadeFromTest' : 'tsNoAI')}</p>{/if}
    <details><summary>{t('tsDetails')}</summary>
      {#if report.rounds.length}
        <div class="scroll"><table><thead><tr><th scope="col">{t('tsColAI')}</th><th scope="col">{fill('tsColFound', { n: sounds.heard })}</th><th scope="col">{fill('tsColFalse', { n: sounds.not_heard })}</th></tr></thead>
          <tbody>{#each report.rounds as row (row.round)}<tr class:current={row.current}><th scope="row">{fill(row.current ? 'tsRoundNow' : 'tsRound', { n: row.round })}</th>
            {#if row.made_from_test}<td colspan="2" class="muted">{t('tsMadeFromTest')}</td>
            {:else}<td>{sounds.heard ? fill('tsCount', { n: row.found ?? 0 }) : '—'}</td><td>{sounds.not_heard ? fill('tsCount', { n: row.false ?? 0 }) : '—'}</td>{/if}</tr>{/each}</tbody></table></div>
        <p class="muted">{fill('tsCounted', { value: report.threshold.toFixed(1) })} {t('tsNote')}</p>
      {/if}
      <p class="muted">{fill(report.fixed ? 'tsSounds' : 'tsSoundsOpen', { total: sounds.heard + sounds.not_heard, heard: sounds.heard, not_heard: sounds.not_heard })}</p>
      {@render list('tsMissed', report.missed)}{@render list('tsFalse', report.false)}{@render list('tsNew', report.new)}
      <details><summary>{fill('tsRecordings', { n: report.recordings.length })}</summary><ul>{#each report.recordings as file}<li>{file}</li>{/each}</ul><p class="muted">{t('tsRecordingsWhy')}</p></details>
    </details>
  {/if}
</section>{/if}
<style>.tests p{margin:12px 0}.scroll{overflow-x:auto}table{border-collapse:collapse;font-size:15px}
  th,td{padding:8px 12px;border-bottom:1px solid rgb(var(--stone-300));text-align:left;vertical-align:top;font-weight:400}thead th{font-weight:600;max-width:240px}
  tbody th{font-size:15px}tr.current th,tr.current td{font-weight:700}details summary{min-height:44px;padding:10px 0;cursor:pointer}
  .sound{display:flex;flex-direction:column;gap:6px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .scores{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px}.score{padding:14px 18px;border:1px solid rgb(var(--stone-300));border-radius:12px}
  .big{font-size:34px;font-weight:700;line-height:1.2}.change{font-size:14px}.good{color:rgb(var(--color-success))}.bad{color:rgb(var(--color-danger))}
  .bars{display:flex;align-items:flex-end;gap:6px;height:54px;margin:10px 0 20px}
  .bars span{position:relative;flex:1;max-width:46px;border-radius:4px 4px 0 0;background:rgb(var(--stone-300))}.bars span.now{background:rgb(var(--primary-700))}
  .bars i{position:absolute;inset:auto 0 -20px;text-align:center;font-size:12px;font-style:normal;color:rgb(var(--stone-600))}</style>
