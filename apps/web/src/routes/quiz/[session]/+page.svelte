<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount, tick } from 'svelte';
  import { page } from '$app/state';
  import { api, mediaURL } from '$lib/api.svelte';
  import { app, t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { quizContext } from '$lib/quiz';
  import type { QuizSession } from '$lib/quiz';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const context = quizContext(page.url);
  const quizUrl = `${resolve('/quiz')}${context.query ? `?${context.query}` : ''}`;
  const returnUrl = context.returnPath ? `${resolve('/datasets')}${context.returnPath.slice('/datasets'.length)}` : '';
  const sessionId = $derived(Number(page.params.session));
  let session = $state<QuizSession | null>(null), loadError = $state('');
  let reviewPosition = $state<number | null>(null), questionPlayer = $state<{play:()=>Promise<void>;stop:()=>void}>();
  let feedbackElement = $state<HTMLDivElement>(), questionHeading = $state<HTMLHeadingElement>();
  let answering = $state<string | null>(null);
  async function load() { session = await api<QuizSession>(`/quiz/sessions/${sessionId}`); }
  const answer = createTask(async (speciesId: string | null) => {
    if (!session?.question) return;
    answering = speciesId ?? 'unknown'; questionPlayer?.stop();
    try {
      session = await api<QuizSession>(`/quiz/sessions/${session.id}/answers`, { position: session.question.position, species_id: speciesId });
      await tick(); feedbackElement?.focus();
    } finally { answering = null; }
  }, { toast: true });
  const advance = createTask(async () => {
    questionPlayer?.stop(); await load(); await tick(); questionHeading?.focus();
  }, { toast: true });
  const replay = createTask(async (position: number) => {
    questionPlayer?.stop(); reviewPosition = position; await tick();
    try { await questionPlayer?.play(); } catch { throw Error('unreadable_audio'); }
  }, { toast: true });
  onMount(() => { load().catch(e => loadError = e instanceof Error ? e.message : 'unknown_error');  });
</script>

<div class="page">
  <div class="page-header"><div><div class="crumbs"><a href={quizUrl}>{t('quiz')}</a><span>›</span></div><h1>{t('quizSession')}</h1></div>
    <div class="actions"><Button href={quizUrl}>‹ {t('backToQuiz')}</Button>{#if returnUrl}<Button href={returnUrl}>{t('wReturn')}</Button>{/if}</div></div>
  {#if loadError}<div class="panel empty"><p class="error">{t(loadError === 'not_found' ? 'sessionNotFound' : loadError)}</p></div>
  {:else if !session}<p class="muted"><Spinner /> {t('loading')}</p>
  {:else}
    <section class="panel quiz-session">
      <div class="section-heading"><h2>{t('quizProgress')}</h2><span class="mono">{session.answered} / {session.count}</span></div>
      <progress max={session.count} value={session.answered} aria-label={t('quizProgress')}></progress>
      {#if session.question}
        {@const question = session.question}
        <h2 class="question" tabindex="-1" bind:this={questionHeading}>{t('quizQuestion')} {question.position + 1}</h2>
        {#if !question.feedback}<p>{t('quizQuestionPrompt')}</p>{/if}
        {#key `${session.id}:${question.position}`}
          <AudioRegion bind:this={questionPlayer} source={{kind:'quiz',id:session.id,position:question.position,seconds:question.seconds}} start={0} end={question.seconds} fixedRange />
        {/key}
        {#if !question.feedback}
          <div class="choices">{#each question.choices as species (species.id)}
            <Button class="choice" size="lg" pending={answer.pending && answering === species.id} disabled={answer.pending} onclick={() => answer.run(species.id)}>
              <span>{commonName(species, app.locale)}<i class="scientific-name">{species.scientific_name}</i></span></Button>
          {/each}
          <Button class="choice unknown" pending={answer.pending && answering === 'unknown'} disabled={answer.pending} onclick={() => answer.run(null)}>{t('quizUnknown')}</Button></div>
        {:else}
          {@const feedback = question.feedback}
          <div class="feedback" aria-live="polite" tabindex="-1" bind:this={feedbackElement}>
            <h2>{t(`quiz_${feedback.status}`)}</h2>
            <p>{t('quizCorrectAnswer')}: <strong>{commonName(feedback.clip.species, app.locale)}</strong> · <i>{feedback.clip.species.scientific_name}</i></p>
            <p>{t('quizYourAnswer')}: {#if feedback.answer}{commonName(feedback.answer, app.locale)} · <i>{feedback.answer.scientific_name}</i>{:else}{t('quizUnknown')}{/if}</p>
            {#if feedback.clip.metadata.image_file}<img class="bird-photo" src={mediaURL(`/quiz/clips/${feedback.clip.id}/image`)} alt={commonName(feedback.clip.species, app.locale)} />{/if}
            <p>{t(`quizSound_${feedback.clip.metadata.sound_type}`)}</p>
            {#if feedback.clip.metadata.note}<p>{feedback.clip.metadata.note}</p>{/if}
            <p class="muted">{t('quizCredit')}: {feedback.clip.metadata.credit.recordist || t('quizCreditMissing')} {feedback.clip.metadata.credit.source_url ?? ''} · {feedback.clip.metadata.credit.license || t('quizLicenseMissing')}</p>
            {#if feedback.clip.metadata.image_file}<p class="muted">{t('quizPhotoCredit')}: {feedback.clip.metadata.credit.photographer || t('quizCreditMissing')} · {feedback.clip.metadata.credit.image_license || t('quizLicenseMissing')}</p>{/if}
            {#if feedback.clip.metadata.xeno_canto}<p class="muted">XC{feedback.clip.metadata.xeno_canto.id} · {feedback.clip.metadata.xeno_canto.recordist} · {feedback.clip.metadata.xeno_canto.license}</p>{/if}
          </div>
          <Button class="next" variant="primary" pending={advance.pending} onclick={() => advance.run()}>{t(session.complete ? 'quizShowResults' : 'quizNext')}</Button>
        {/if}
      {:else}
        <h2 class="question" tabindex="-1" bind:this={questionHeading}>{t('quizResults')}</h2>
        <p class="result-score">{session.correct} / {session.answered} · {(session.correct / session.answered * 100).toFixed(1)}%</p>
        <p>{t('quiz_incorrect')}: {session.answered - session.correct - session.unknown} · {t('quizUnknown')}: {session.unknown}</p>
        <p class="muted">{t('quizResultHint')}</p>
        <div class="table-wrap"><table><thead><tr><th>{t('species')}</th><th>{t('quiz_correct')}</th><th>{t('quiz_incorrect')}</th><th>{t('quizUnknown')}</th><th>{t('quizTotal')}</th></tr></thead><tbody>
          {#each session.per_species as row (row.species.id)}<tr><td>{commonName(row.species, app.locale)}<i class="scientific-name">{row.species.scientific_name}</i></td><td>{row.correct}</td><td>{row.incorrect}</td><td>{row.unknown}</td><td>{row.total}</td></tr>{/each}
        </tbody></table></div>
        <h2 class="review-heading">{t('quizReview')}</h2>
        <div class="review-list">{#each session.review as row (row.position)}<div class="list-item"><span>{row.position + 1}. {commonName(row.clip.species, app.locale)} · <i>{row.clip.species.scientific_name}</i> · {t(`quiz_${row.status}`)}<br />
          {t('quizYourAnswer')}: {#if row.answer}{commonName(row.answer, app.locale)} · <i>{row.answer.scientific_name}</i>{:else}{t('quizUnknown')}{/if}</span>
          <Button size="sm" pending={replay.pending && reviewPosition === row.position} disabled={replay.pending} onclick={() => replay.run(row.position)}>{t('play')}</Button></div>{/each}</div>
        {#if reviewPosition !== null}{#key reviewPosition}
          {@const review = session.review.find(row=>row.position===reviewPosition)!}
          <AudioRegion bind:this={questionPlayer} source={{kind:'quiz',id:session.id,position:review.position,seconds:review.clip.seconds}} start={0} end={review.clip.seconds} fixedRange />
        {/key}{/if}
      {/if}
      {#if answer.error || advance.error || replay.error}<p class="error" role="alert">{t(answer.error || advance.error || replay.error)}</p>{/if}
    </section>
  {/if}
</div>

<style>
  .quiz-session { max-width: 960px; width: 100%; align-self: center; }
  .question { margin-top: 20px; }
  .choices { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin: 20px 0; }
  .choices :global(.choice) { justify-content: flex-start; text-align: left; white-space: normal; min-height: 76px; }
  .choices :global(.unknown) { min-height: 50px; }
  .feedback { display: flex; flex-direction: column; align-items: flex-start; gap: 10px; border: 1px solid rgb(var(--primary-300)); padding: 18px; border-radius: 6px; }
  .bird-photo { max-width: 100%; width: 320px; max-height: 240px; object-fit: contain; border-radius: 4px; }
  :global(.next) { margin-top: 16px; }
  .result-score { margin: 12px 0; font-size: 28px; color: rgb(var(--primary-700)); }
  .review-heading { margin-top: 20px; }
  .review-list { max-height: 340px; overflow: auto; }
</style>
