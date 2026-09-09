<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import AudioRegion, { type AudioSource } from './AudioRegion.svelte';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import XenoCantoSearch from './XenoCantoSearch.svelte';
  import type { QuizClip } from './quiz';
  import type { Species } from './species';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  let { initial = null, onadded }: { initial?: QuizClip | null; onadded: (clip: QuizClip) => void | Promise<void> } = $props();
  let species = $state<Species | null>(untrack(() => initial?.species ?? null));
  let source = $state<AudioSource | null>(untrack(() => initial?.source ?? null));
  let path = $state(''), playerKey = $state(0);
  let start = $state(0), end = $state(untrack(() => Math.min(5, source?.seconds ?? 5)));
  let soundType = $state(untrack(() => initial?.metadata.sound_type ?? 'other'));
  let note = $state(untrack(() => initial?.metadata.note ?? ''));
  let credit = $state(untrack(() => ({ recordist: initial?.metadata.credit.recordist ?? '',
    source_url: initial?.metadata.credit.source_url ?? '', license: initial?.metadata.credit.license ?? '' })));
  let restored=$state(false);
  onMount(()=>{if(!initial){try{const saved=JSON.parse(sessionStorage.getItem('echoroo-quiz-audio')||'null');if(saved)({species,source,path,start,end,credit}=saved);}catch{}}restored=true;});
  $effect(()=>{if(restored&&!initial)sessionStorage.setItem('echoroo-quiz-audio',JSON.stringify({species,source,path,start,end,credit}));});
  const name = (value: string) => value.split(/[\\/]/).pop();
  const valid = $derived(source && species && Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end <= source.seconds && end - start >= .2 - 1e-9 && end - start <= 60 + 1e-9);
  function select(row: AudioSource) {
    source = row; start = 0; end = Math.min(5, row.seconds); playerKey++;
    credit = { recordist: '', source_url: '', license: '' }; note = '';
  }
  const addReference = createTask(async () => {
    if (isTauri()) {
      const picked = await open({ multiple: false, filters: [{ name: t('referenceAudio'), extensions: ['wav', 'flac', 'mp3'] }] });
      if (!picked) return;
      path = picked;
    }
    select(await api<AudioSource>('/references', { path }));
  }, { toast: true });
  const save = createTask(async () => {
    if (!source || !species) return;
    const clip=await api<QuizClip>('/quiz/clips', { species_id: species.id, source_kind: source.kind, source_id: source.id,
      start_s: start, end_s: end, sound_type: soundType, note, credit });
    restored=false;sessionStorage.removeItem('echoroo-quiz-audio');await onadded(clip);
  }, { toast: true });
</script>

<section class="editor" aria-label={t('quizAddAudio')}>

  <SpeciesPicker bind:value={species} disabled={save.pending} />
  <details class="xc-panel"><summary>{t('xcFind')}</summary><XenoCantoSearch {species} onselect={select} /></details>
  <div class="source-controls"><Button pending={addReference.pending} disabled={!isTauri() && !path.trim()} onclick={() => addReference.run()}>{t('addReference')}</Button></div>
  {#if !isTauri()}<label>{t('referencePath')}<input bind:value={path} /></label>{/if}
  {#if source}
    <p class="muted">{name(source.path)}</p>
    {#key playerKey}<AudioRegion {source} bind:start bind:end />{/key}
    <details><summary>{t('quizCredit')}</summary><div class="form-grid">
      <label>{t('quizRecordist')}<input bind:value={credit.recordist} maxlength="200" /></label>
      <label>{t('quizSourceURL')}<input bind:value={credit.source_url} maxlength="1000" /></label>
      <label>{t('quizLicense')}<input bind:value={credit.license} maxlength="1000" /></label>
    </div></details>
    <p class="muted">{t('quizLabelHint')}</p>
    <Button variant="primary" pending={save.pending} disabled={!valid} onclick={() => save.run()}>{t('quizSaveClip')}</Button>
  {/if}
  {#if addReference.error || save.error}<p class="error" role="alert">{t(addReference.error || save.error)}</p>{/if}
</section>

<style>
  .editor { padding: 0; }
  .source-controls { display: flex; gap: 10px; margin: 14px 0; }
  details { margin: 14px 0; }
  .xc-panel { padding: 10px 12px; border: 1px solid rgb(var(--stone-300)); border-radius: 6px; }
</style>
