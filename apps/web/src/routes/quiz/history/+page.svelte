<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { quizContext,type QuizLibrary } from '$lib/quiz';
  import Button from '$lib/ui/Button.svelte';
  let library=$state<QuizLibrary|null>(null),error=$state('');
  const context=quizContext(page.url),query=context.query?`?${context.query}`:'';
  // Stored in UTC as "YYYY-MM-DD HH:MM:SS".
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(`${value.replace(' ','T')}Z`));
  onMount(()=>{api<QuizLibrary>('/quiz/library').then(v=>library=v).catch(e=>error=e.message);});
</script>
<div class="page narrow"><div class="crumbs"><a href={`${resolve('/quiz')}${query}`}>{t('uBackQuiz')}</a></div><h1>{t('uQuizHistory')}</h1>
  {#if error}<p class="error">{t(error)}</p>{/if}
  {#each library?.sessions??[] as session}<section class="panel section-heading"><span>{when(session.created_at)} · {session.answered}/{session.count}</span><Button href={`${resolve(`/quiz/${session.id}`)}${query}`}>{t(session.answered===session.count?'open':'quizResume')}</Button></section>
  {:else}{#if library}<section class="panel empty"><p>{t('quizHistoryEmpty')}</p><Button variant="primary" href={`${resolve('/quiz')}${query}`}>{t('quizStart')}</Button></section>{/if}{/each}
</div>
