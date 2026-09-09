<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from './api';
  import AudioRegion, { type AudioSource } from './AudioRegion.svelte';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import XenoCanto from './XenoCanto.svelte';
  import type { QuizClip } from './quiz';
  import type { Species } from './species';
  let { recording, initial = null, locale, t, onadded }: { recording: Omit<AudioSource,'kind'> | null;
    initial?: QuizClip | null; locale: string; t: (key: string) => string; onadded: (clip: QuizClip) => void } = $props();
  let species = $state<Species | null>(untrack(() => initial?.species ?? null));
  let source = $state<AudioSource | null>(untrack(() => initial?.source ?? (recording ? {...recording,kind:'recording'} : null)));
  let references = $state<AudioSource[]>([]), referenceId = $state(''), path = $state(''), key = $state(0);
  let start = $state(0), end = $state(untrack(() => Math.min(5, source?.seconds ?? 5)));
  let soundType = $state(untrack(() => initial?.metadata.sound_type ?? 'other'));
  let note = $state(''), credit = $state(untrack(() => ({recordist:initial?.metadata.credit.recordist ?? '',
    source_url:initial?.metadata.credit.source_url ?? '', license:initial?.metadata.credit.license ?? ''})));
  let busy = $state(false), error = $state('');
  const name = (value: string) => value.split(/[\\/]/).pop();
  const valid = $derived(source && species && Number.isFinite(start) && Number.isFinite(end) && start>=0 && end<=source.seconds && end-start>=.2-1e-9 && end-start<=60+1e-9);
  function select(row: AudioSource) {
    source=row; start=0; end=Math.min(5,row.seconds); key++;
    credit={recordist:'',source_url:'',license:''}; note='';
  }
  async function action(fn: () => Promise<void>) {
    busy=true; error=''; try { await fn(); } catch(e) { error=e instanceof Error ? e.message : 'unknown_error'; } finally { busy=false; }
  }
  async function addReference() {
    if(isTauri()) { const picked=await open({multiple:false,filters:[{name:t('referenceAudio'),extensions:['wav','flac','mp3']}]}); if(!picked) return; path=picked; }
    select(await api<AudioSource>('/references',{path}));
  }
  async function save() {
    if(!source || !species) return;
    onadded(await api<QuizClip>('/quiz/clips',{species_id:species.id, source_kind:source.kind,source_id:source.id,
      start_s:start,end_s:end,sound_type:soundType,note,credit}));
  }
  onMount(() => { void action(async () => { references=await api<AudioSource[]>('/references'); }); });
</script>

<section class="editor" aria-label={t('quizAddAudio')}>
  <h3>{t('quizAddAudio')}</h3><p class="muted">{t('quizAddHint')}</p>
  <SpeciesPicker bind:value={species} {locale} {t} disabled={busy}/>
  <XenoCanto {species} {locale} {t} onselect={select}/>
  <div class="source-controls"><button disabled={busy} onclick={() => action(addReference)}>{t('addReference')}</button>
    {#if recording}<button class="small" disabled={busy} onclick={() => select({...recording,kind:'recording'})}>{t('currentRecording')}</button>{/if}</div>
  {#if !isTauri()}<label>{t('referencePath')}<input bind:value={path}/></label>{/if}
  {#if references.length}<label>{t('referenceSource')}<select bind:value={referenceId} disabled={busy} onchange={() => { const row=references.find(r=>r.id===Number(referenceId)); if(row) select(row); }}>
    <option value="">{t('quizChooseAudio')}</option>{#each references as ref}<option value={String(ref.id)}>{name(ref.path)}</option>{/each}</select></label>{/if}
  {#if source}<p class="muted">{name(source.path)}</p>{#key key}<AudioRegion {source} {t} bind:start bind:end/>{/key}
    <label>{t('quizSoundType')}<select bind:value={soundType} disabled={busy}>{#each ['song','call','other'] as kind}<option value={kind}>{t(`quizSound_${kind}`)}</option>{/each}</select></label>
    <label>{t('quizNote')}<textarea bind:value={note} maxlength="2000" rows="2" disabled={busy}></textarea></label>
    <details><summary>{t('quizCredit')}</summary><div class="form-grid">
      <label>{t('quizRecordist')}<input bind:value={credit.recordist} maxlength="200"/></label>
      <label>{t('quizSourceURL')}<input bind:value={credit.source_url} maxlength="1000"/></label>
      <label>{t('quizLicense')}<input bind:value={credit.license} maxlength="1000"/></label>
    </div></details><p class="muted">{t('quizLabelHint')}</p>
    <button class="primary" disabled={busy || !valid} onclick={() => action(save)}>{t(busy?'quizSaving':'quizSaveClip')}</button>
  {/if}
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
</section>

<style>
  .editor { border:1px solid rgb(var(--stone-300)); border-radius:6px; padding:18px; margin:18px 0; }
  .source-controls { display:flex; gap:10px; margin:14px 0; }
  textarea { width:100%; color:inherit; background:rgb(var(--stone-50)); border:1px solid rgb(var(--stone-300)); border-radius:4px; padding:8px; }
  details { margin:14px 0; }
</style>
