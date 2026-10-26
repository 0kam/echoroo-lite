<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import { quizContext } from '$lib/quiz';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app, t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import type { QuizLibrary, QuizSession } from '$lib/quiz';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const context = quizContext(page.url);
  const returnUrl = context.returnPath ? `${resolve('/datasets')}${context.returnPath.slice('/datasets'.length)}` : '';
  const continuation = context.query ? `?${context.query}` : '';
  let library = $state<QuizLibrary | null>(null), selected = $state<number[]>([]);
  const enabled = $derived((library?.clips ?? []).filter(clip => clip.enabled));
  const focused = $derived(enabled.find(clip => clip.species_id === context.species));
  // Nothing ships with the app: until the user has added calls of two birds, adding them is the only task.
  const ready = $derived(new Set(enabled.map(clip => clip.species_id)).size >= 2);
  let count = $state(10), loadError = $state('');
  const speciesCount = $derived(new Set(enabled.filter(clip => selected.includes(clip.id)).map(clip => clip.species_id)).size);
  async function refresh() {
    library = await api<QuizLibrary>('/quiz/library');
    selected = selected.filter(id => enabled.some(clip => clip.id === id));
  }
  const start = createTask(async () => {
    const session = await api<QuizSession>('/quiz/sessions', { clip_ids: selected, count });
    await refresh();
    await goto(`${resolve(`/quiz/${session.id}`)}${continuation}`);
  }, { toast: true });
  onMount(() => { void (async () => {
    try { await refresh(); selected = enabled.map(c => c.id); if(focused){const others=enabled.filter(c=>c.species_id!==context.species);selected=[focused.id,...others.filter((c,i)=>others.findIndex(x=>x.species_id===c.species_id)===i).slice(0,3).map(c=>c.id)];} }
    catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
  })(); });
</script>
<div class="page narrow quiz-home">
  {#if returnUrl}<div class="crumbs"><a href={returnUrl}>{t('wReturn')}</a></div>{/if}
  <h1>{t('quiz')}</h1><p>{t('uQuizHint')}</p>
  {#if loadError}<p class="error" role="alert">{t(loadError)}</p>
  {:else if !library}<p role="status">{t('loading')}</p>
  {:else}
    {#if focused}<section class="panel"><h2>{commonName(focused.species,app.locale)}</h2><p>{t('wPractice')}</p><AudioRegion source={focused.source} start={0} end={focused.seconds} fixedRange/></section>{/if}
    {#if ready}
      <section class="panel"><h2>{context.species?`${speciesCount} ${t('uBirdQuiz')}`:t('uQuizAll')}</h2>
        <Button variant="primary" size="lg" pending={start.pending} disabled={speciesCount<2} onclick={()=>start.run()}>{t('quizStart')}</Button>
        {#if start.error}<p class="error" role="alert">{t(start.error)}</p>{/if}
      </section>
    {:else}
      <section class="panel"><p>{t('uQuizEmptyHint')}</p>
        <Button variant="primary" size="lg" href={`${resolve('/quiz/add')}${continuation}`}>{t('uQuizAdd')}</Button>
      </section>
    {/if}
    {@const unfinished=library.sessions.find(s=>s.answered<s.count)}
    {#if unfinished&&!context.species}<Button href={`${resolve(`/quiz/${unfinished.id}`)}${continuation}`}>{t('uQuizResume')}</Button>{/if}
    <div class="actions">
      {#if ready}<Button href={`${resolve('/quiz/choose')}${continuation}`}>{t('uQuizCustom')}</Button><Button href={`${resolve('/quiz/add')}${continuation}`}>{t('uQuizAdd')}</Button>{/if}
      {#if library.sessions.length}<Button href={`${resolve('/quiz/history')}${continuation}`}>{t('uQuizHistory')}</Button>{/if}
    </div>
    <p class="muted">{t('quizPracticeHint')}</p>
  {/if}
</div>
<style>p{margin:16px 0}</style>
