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
  let library = $state<QuizLibrary | null>(null), selected = $state<number[]>([]), setId = $state('');
  const focused = $derived(library?.clips.find(clip => clip.enabled && clip.species_id === context.species));
  let count = $state(10), loadError = $state('');
  const speciesCount = $derived(new Set((library?.clips ?? []).filter(clip => selected.includes(clip.id) && clip.enabled).map(clip => clip.species_id)).size);
  async function refresh() {
    library = await api<QuizLibrary>('/quiz/library');
    selected = selected.filter(id => library!.clips.some(clip => clip.id === id && clip.enabled));
  }
  function chooseSet() {
    const ids = setId ? library?.sets.find(set => set.id === Number(setId))?.clip_ids ?? [] : library?.clips.map(clip => clip.id) ?? [];
    selected = ids.filter(id => library?.clips.some(clip => clip.id === id && clip.enabled));
  }
  const start = createTask(async () => {
    const session = await api<QuizSession>('/quiz/sessions', { clip_ids: selected, count });
    await refresh();
    await goto(`${resolve(`/quiz/${session.id}`)}${continuation}`);
  }, { toast: true });
  onMount(() => { void (async () => {
    try { await refresh(); if (library) { setId = String(library.sets.find(s=>s.bundled_key)?.id??''); chooseSet(); } if(context.species){const target=library?.clips.find(c=>c.enabled&&c.species_id===context.species);if(target){const others=(library?.clips??[]).filter(c=>c.enabled&&c.species_id!==context.species);selected=[target.id,...others.filter((c,i)=>others.findIndex(x=>x.species_id===c.species_id)===i).slice(0,3).map(c=>c.id)];setId='';}} }
    catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
  })(); });
</script>
<div class="page quiz-home">
  {#if returnUrl}<div class="crumbs"><a href={returnUrl}>{t('wReturn')}</a></div>{/if}
  <h1>{t('quiz')}</h1><p>{t('uQuizHint')}</p>
  {#if loadError}<p class="error" role="alert">{t(loadError)}</p>
  {:else if !library}<p role="status">{t('loading')}</p>
  {:else}
    {#if focused}<section class="panel"><h2>{commonName(focused.species,app.locale)}</h2><p>{t('wPractice')}</p><AudioRegion source={focused.source} start={0} end={focused.seconds} fixedRange/></section>{/if}
    <section class="panel"><h2>{context.species?`${speciesCount} ${t('uBirdQuiz')}`:t('uQuizAll')}</h2>
      <Button variant="primary" size="lg" pending={start.pending} disabled={speciesCount<2} onclick={()=>start.run()}>{t('quizStart')}</Button>
      {#if start.error}<p class="error" role="alert">{t(start.error)}</p>{/if}
    </section>
    {@const unfinished=library.sessions.find(s=>s.answered<s.count)}
    {#if unfinished&&!context.species}<Button href={`${resolve(`/quiz/${unfinished.id}`)}${continuation}`}>{t('uQuizResume')}</Button>{/if}
    <div class="actions"><Button href={`${resolve('/quiz/choose')}${continuation}`}>{t('uQuizCustom')}</Button><Button href={`${resolve('/quiz/add')}${continuation}`}>{t('uQuizAdd')}</Button><Button href={`${resolve('/quiz/history')}${continuation}`}>{t('uQuizHistory')}</Button></div>
    <p class="muted">{t('quizPracticeHint')}</p>
  {/if}
</div>
<style>.quiz-home{max-width:800px!important}p{margin:16px 0}</style>
