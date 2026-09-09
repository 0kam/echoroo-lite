<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { api, connection } from './api';
  import { commonName } from './species';
  import type { QuizClip, QuizLibrary, QuizSession } from './quiz';
  import AudioRegion, { clipTime, type AudioSource } from './AudioRegion.svelte';
  import QuizEditor from './QuizEditor.svelte';
  let { recording, locale, t }: {recording: Omit<AudioSource,'kind'> | null; locale: string; t: (key: string) => string} = $props();
  let library = $state<QuizLibrary | null>(null), selected = $state<number[]>([]), setId = $state(''), setName = $state('');
  let filter = $state(''), count = $state(10), includeDisabled = $state(false), sessionId = $state('');
  let session = $state<QuizSession | null>(null), busy = $state(false), error = $state(''), notice = $state('');
  let showEditor = $state(false), editing = $state<QuizClip | null>(null), editorKey = $state(0), inspect = $state(false);
  let reviewPosition = $state<number | null>(null), questionAudio = $state<HTMLAudioElement>();
  let feedbackElement = $state<HTMLDivElement>(), questionHeading = $state<HTMLHeadingElement>();
  const visible = $derived((library?.clips ?? []).filter(c => (c.enabled || includeDisabled) &&
    `${commonName(c.species,locale)} ${c.species.scientific_name}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase())).sort((a,b)=>commonName(a.species,locale).localeCompare(commonName(b.species,locale),locale)));
  const speciesCount = $derived(new Set((library?.clips ?? []).filter(c=>selected.includes(c.id)&&c.enabled).map(c=>c.species_id)).size);
  const media = (path: string) => `${connection.url}${path}?token=${encodeURIComponent(connection.token)}`;
  async function action(fn: () => Promise<void>) {
    busy=true; error=''; try { await fn(); } catch(e) { error=e instanceof Error ? e.message : 'unknown_error'; } finally { busy=false; }
  }
  async function refresh() {
    library=await api<QuizLibrary>('/quiz/library');
    selected=selected.filter(id=>library!.clips.some(c=>c.id===id&&c.enabled));
  }
  function chooseSet() {
    const ids = setId ? library?.sets.find(s=>s.id===Number(setId))?.clip_ids ?? [] : library?.clips.map(c=>c.id) ?? [];
    selected=ids.filter(id=>library?.clips.some(c=>c.id===id&&c.enabled));
  }
  function edit(clip: QuizClip | null) { editing=clip; editorKey++; showEditor=true; session=null; notice=''; }
  async function answer(species_id: string | null) {
    questionAudio?.pause();
    session=await api<QuizSession>(`/quiz/sessions/${session!.id}/answers`,{position:session!.question!.position,species_id});
    inspect=false; await tick(); feedbackElement?.focus();
  }
  async function advance() { questionAudio?.pause(); inspect=false; session=await api<QuizSession>(`/quiz/sessions/${session!.id}`); await tick(); questionHeading?.focus(); }
  async function replay(position: number) {
    questionAudio?.pause(); reviewPosition=position; await tick();
    if(questionAudio) {
      questionAudio.currentTime=0;
      try { await questionAudio.play(); } catch { throw Error('unreadable_audio'); }
    }
  }
  async function start() {
    session=await api<QuizSession>('/quiz/sessions',{clip_ids:selected,count}); sessionId=String(session.id);
    showEditor=false; inspect=false; reviewPosition=null; await refresh();
  }
  async function added(clip: QuizClip) {
    showEditor=false; editing=null; await refresh(); selected=[...selected.filter(id=>id!==clip.id),clip.id]; setId=''; notice='quizClipAdded';
  }
  onMount(() => { void action(async () => { await refresh(); setId=String(library!.sets[0].id); chooseSet(); }); return () => questionAudio?.pause(); });
</script>

<section class="panel quiz-panel">
  <div class="section-heading"><h2>{t('quiz')}</h2><button class="small" disabled={busy} onclick={() => { questionAudio?.pause(); session=null; void action(refresh); }}>{t('quizChooseQuestions')}</button></div>
  <p class="notice">{t('quizPracticeHint')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if notice}<p class="notice" role="status">{t(notice)}</p>{/if}
  {#if !library}<p role="status">{t('quizLoading')}</p>
  {:else if session}
    <h3>{t('quizSession')} #{session.id} · {session.answered} / {session.count}</h3>
    <progress max={session.count} value={session.answered} aria-label={t('quizProgress')}></progress>
    {#if session.question}
      {@const question=session.question}
      <h3 tabindex="-1" bind:this={questionHeading}>{t('quizQuestion')} {question.position+1}</h3>{#if !question.feedback}<p>{t('quizQuestionPrompt')}</p>{/if}
      {#key `${session.id}:${question.position}`}
        <!-- Bird-call practice audio has no speech transcript. -->
        <!-- svelte-ignore a11y_media_has_caption -->
        <audio bind:this={questionAudio} controls preload="metadata" aria-label={t('quizQuestionAudio')}
          src={media(`/quiz/sessions/${session.id}/questions/${question.position}/audio`)} onerror={() => error='unreadable_audio'}></audio>
      {/key}
      {#if !question.feedback}<div class="choices">{#each question.choices as species}<button class="choice" disabled={busy}
        onclick={() => action(() => answer(species.id))}>{commonName(species,locale)}<i class="scientific-name">{species.scientific_name}</i></button>{/each}
        <button class="choice unknown" disabled={busy} onclick={() => action(() => answer(null))}>{t('quizUnknown')}</button></div>{/if}
      {#if question.feedback}
        {@const feedback=question.feedback}
        <div class="feedback" aria-live="polite" tabindex="-1" bind:this={feedbackElement}><h3>{t(`quiz_${feedback.status}`)}</h3><p>{t('quizCorrectAnswer')}: <strong>{commonName(feedback.clip.species,locale)}</strong> · <i>{feedback.clip.species.scientific_name}</i></p>
          <p>{t('quizYourAnswer')}: {#if feedback.answer}{commonName(feedback.answer,locale)} · <i>{feedback.answer.scientific_name}</i>{:else}{t('quizUnknown')}{/if}</p>
          {#if feedback.clip.metadata.image_file}<img class="bird-photo" src={media(`/quiz/clips/${feedback.clip.id}/image`)} alt={commonName(feedback.clip.species,locale)}/>{/if}
          <p>{t(`quizSound_${feedback.clip.metadata.sound_type}`)}</p>{#if feedback.clip.metadata.note}<p>{feedback.clip.metadata.note}</p>{/if}
          <p class="muted">{t('quizCredit')}: {feedback.clip.metadata.credit.recordist || t('quizCreditMissing')}
            {feedback.clip.metadata.credit.source_url ?? ''} · {feedback.clip.metadata.credit.license || t('quizLicenseMissing')}</p>
          {#if feedback.clip.metadata.image_file}<p class="muted">{t('quizPhotoCredit')}: {feedback.clip.metadata.credit.photographer || t('quizCreditMissing')} · {feedback.clip.metadata.credit.image_license || t('quizLicenseMissing')}</p>{/if}
          {#if feedback.clip.metadata.xeno_canto}<p class="muted">XC{feedback.clip.metadata.xeno_canto.id} · {feedback.clip.metadata.xeno_canto.recordist} · {feedback.clip.metadata.xeno_canto.license}</p>{/if}
          <button class="small" onclick={() => inspect=!inspect}>{t('quizInspectAudio')}</button>
          {#if inspect}<AudioRegion source={feedback.clip.source} {t} start={0} end={feedback.clip.seconds}/>{/if}
        </div><button class="primary next" disabled={busy} onclick={() => action(advance)}>{t(session.complete?'quizShowResults':'quizNext')}</button>
      {/if}
    {:else}
      <h3 tabindex="-1" bind:this={questionHeading}>{t('quizResults')}</h3>
      <p class="result-score">{session.correct} / {session.answered} · {(session.correct/session.answered*100).toFixed(1)}%</p>
      <p>{t('quiz_incorrect')}: {session.answered-session.correct-session.unknown} · {t('quizUnknown')}: {session.unknown}</p><p class="muted">{t('quizResultHint')}</p>
      <table><thead><tr><th>{t('species')}</th><th>{t('quiz_correct')}</th><th>{t('quiz_incorrect')}</th><th>{t('quizUnknown')}</th><th>{t('quizTotal')}</th></tr></thead><tbody>
        {#each session.per_species as row}<tr><td>{commonName(row.species,locale)}<i class="scientific-name">{row.species.scientific_name}</i></td><td>{row.correct}</td><td>{row.incorrect}</td><td>{row.unknown}</td><td>{row.total}</td></tr>{/each}
      </tbody></table>
      <h3>{t('quizReview')}</h3><div class="review-list">{#each session.review as row}<div class="review-row"><span>{row.position+1}. {commonName(row.clip.species,locale)} · <i>{row.clip.species.scientific_name}</i> · {t(`quiz_${row.status}`)}<br/>
        {t('quizYourAnswer')}: {#if row.answer}{commonName(row.answer,locale)} · <i>{row.answer.scientific_name}</i>{:else}{t('quizUnknown')}{/if}</span>
        <button class="small" disabled={busy} onclick={() => action(() => replay(row.position))}>{t('play')}</button></div>{/each}</div>
      {#if reviewPosition!==null}{#key reviewPosition}
        <!-- svelte-ignore a11y_media_has_caption -->
        <audio bind:this={questionAudio} controls preload="metadata" aria-label={`${t('quizQuestionAudio')} ${reviewPosition+1}`}
          src={media(`/quiz/sessions/${session.id}/questions/${reviewPosition}/audio`)} onerror={() => error='unreadable_audio'}></audio>
      {/key}{/if}
    {/if}
  {:else}
    <div class="form-grid"><label>{t('quizSet')}<select bind:value={setId} disabled={busy} onchange={chooseSet}><option value="">{t('quizAllClips')}</option>
      {#each library.sets as set}<option value={String(set.id)}>{set.bundled_key ? t('quizSampleSet') : set.name}</option>{/each}</select></label>
      <label>{t('quizCount')}<input type="number" min="1" max="50" step="1" bind:value={count} disabled={busy}/></label></div>
    <p class="muted">{t('quizSelection')}: {selected.length} {t('quizClips')} · {speciesCount} {t('quizSpecies')} · {t('quizSamplingHint')}</p>
    <div class="toolbar"><button class="primary" disabled={busy || speciesCount<2 || !Number.isInteger(count) || count<1 || count>50} onclick={() => action(start)}>{t('quizStart')}</button>
      <button disabled={busy} onclick={() => edit(null)}>{t('quizAddAudio')}</button></div>
    <details class="selection-list"><summary>{t('quizSelectSpecies')}</summary>
      <label>{t('quizFilter')}<input bind:value={filter}/></label>
      <div class="toolbar"><button class="small" disabled={busy} onclick={() => {selected=[...new Set([...selected,...visible.filter(c=>c.enabled).map(c=>c.id)])];}}>{t('quizSelectVisible')}</button>
        <button class="small" disabled={busy} onclick={() => selected=selected.filter(id=>!visible.some(c=>c.id===id))}>{t('quizClearVisible')}</button>
        <label class="checkbox"><input type="checkbox" bind:checked={includeDisabled}/>{t('quizIncludeDisabled')}</label></div>
      <div class="clip-list">{#each visible as clip}<div class="clip-row"><label class="checkbox"><input type="checkbox" value={clip.id} bind:group={selected} disabled={busy||!clip.enabled}/>
        <span>{commonName(clip.species,locale)}<i class="scientific-name">{clip.species.scientific_name}</i><span class="muted">#{clip.id} · {t(`quizSound_${clip.metadata.sound_type}`)} · {clipTime(clip.seconds)}</span></span></label>
        <div><button class="small" disabled={busy} onclick={() => edit(clip)}>{t('quizListenAndSelect')}</button>
          <button class="small" disabled={busy} onclick={() => action(async () => {await api(`/quiz/clips/${clip.id}/enabled`,{enabled:!clip.enabled});await refresh();})}>{t(clip.enabled?'quizDisable':'quizEnable')}</button></div></div>{/each}</div>
      <form class="save-set" onsubmit={e => {e.preventDefault();void action(async () => {const result=await api<{id:number}>('/quiz/sets',{name:setName,clip_ids:selected});await refresh();setId=String(result.id);setName='';notice='quizSetSaved';});}}>
        <label>{t('quizSetName')}<input bind:value={setName} required maxlength="200"/></label><button disabled={busy||!setName.trim()||!selected.length}>{t('quizSaveSet')}</button></form>
    </details>
    {#if showEditor}{#key editorKey}<QuizEditor {recording} initial={editing} {locale} {t} onadded={clip => {void action(() => added(clip));}}/>{/key}
      <button class="small" onclick={() => showEditor=false}>{t('quizCloseEditor')}</button>{/if}
    {#if library.sessions.length}<label class="history">{t('quizHistory')}<select bind:value={sessionId} disabled={busy} onchange={() => action(async () => {if(sessionId) {session=await api<QuizSession>(`/quiz/sessions/${sessionId}`);inspect=false;reviewPosition=null;}})}>
      <option value="">{t('quizChooseSession')}</option>{#each library.sessions as row}<option value={String(row.id)}>#{row.id} · {row.answered}/{row.count} · {t(row.answered===row.count?'quizCompleted':'quizResume')}</option>{/each}</select></label>{/if}
  {/if}
</section>

<style>
  .toolbar { display:flex; align-items:center; flex-wrap:wrap; gap:12px; margin:14px 0; }
  .selection-list { margin-top:20px; }
  .selection-list summary { margin-bottom:14px; }
  .clip-list { max-height:440px; overflow:auto; border-block:1px solid rgb(var(--stone-300)); }
  .clip-row,.review-row { display:flex; align-items:center; justify-content:space-between; gap:12px; padding:10px 0; border-bottom:1px solid rgb(var(--stone-200)); }
  .clip-row>div { display:flex; gap:8px; }
  .checkbox { display:flex; align-items:center; gap:10px; }
  .checkbox input { width:18px; height:18px; }
  .choices { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:12px; margin:20px 0; }
  .choice { text-align:left; padding:18px; font-size:16px; min-height:76px; }
  .choice.unknown { min-height:50px; font-size:14px; }
  .feedback { border:1px solid rgb(var(--primary-300)); padding:18px; border-radius:6px; }
  .bird-photo { max-width:100%; width:320px; max-height:240px; object-fit:contain; border-radius:4px; }
  .feedback .muted { overflow-wrap:anywhere; }
  .save-set { display:flex; align-items:end; gap:12px; margin:16px 0; }
  .history { margin-top:22px; }
  progress { width:100%; }
  audio { width:100%; margin:12px 0; }
  .next { margin-top:16px; }
  .result-score { font-size:28px; color:rgb(var(--primary-700)); }
  .review-list { max-height:340px; overflow:auto; }
</style>
