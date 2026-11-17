<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { notify } from '$lib/ui/toast.svelte';
  import { quizContext } from '$lib/quiz';
  import QuizEditor from '$lib/QuizEditor.svelte';
  const context=quizContext(page.url),back=resolve('/quiz')+(context.query?`?${context.query}`:'');
</script>
<div class="page narrow"><div class="crumbs">{#if context.back}<a href={context.back}>{t(context.backLabel)}</a>{:else}<a href={back}>{t('uBackQuiz')}</a>{/if}</div>
  <div class="page-header"><div><h1>{t('uQuizAdd')}</h1><p>{t('uQuizAddHint')}</p></div></div>
  <section class="panel"><QuizEditor suggest={context.species} onadded={clip=>{notify('uQuizClipAdded','success',undefined,{name:commonName(clip.species,app.locale)||clip.species.scientific_name});return goto(`${resolve('/quiz/choose')}?${context.query?context.query+'&':''}added=${clip.id}`);}}/></section>
</div>

