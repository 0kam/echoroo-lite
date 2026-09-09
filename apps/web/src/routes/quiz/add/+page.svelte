<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { t } from '$lib/app.svelte';
  import { quizContext } from '$lib/quiz';
  import QuizEditor from '$lib/QuizEditor.svelte';
  const context=quizContext(page.url),back=resolve('/quiz')+(context.query?`?${context.query}`:'');
</script>
<div class="page quiz-add"><div class="crumbs"><a href={back}>{t('uBackQuiz')}</a></div>
  <h1>{t('uQuizAdd')}</h1><p>{t('uQuizAddHint')}</p>
  <section class="panel"><QuizEditor onadded={clip=>goto(`${resolve('/quiz/choose')}?${context.query?context.query+'&':''}added=${clip.id}`)}/></section>
</div>
<style>.quiz-add{max-width:860px!important}</style>
