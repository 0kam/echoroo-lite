<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { quizContext } from '$lib/quiz';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app, t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import type { QuizLibrary, QuizSession } from '$lib/quiz';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  const context = quizContext(page.url);
  const returnUrl = context.returnPath ? `${resolve('/datasets')}${context.returnPath.slice('/datasets'.length)}` : '';
  const continuation = context.query ? `?${context.query}` : '';
  let library = $state<QuizLibrary | null>(null), selected = $state<number[]>([]), setId = $state(''), setName = $state('');
  const draftKey=`echoroo-quiz-selection-${context.species||'all'}`;
  let restored=$state(false);
  $effect(()=>{if(restored)sessionStorage.setItem(draftKey,JSON.stringify({selected,setId,filter}));});
  let filter = $state(''), count = $state(10), includeDisabled = $state(false), loadError = $state('');
  const visible = $derived((library?.clips ?? []).filter(clip => (clip.enabled || includeDisabled) &&
    `${commonName(clip.species, app.locale)} ${clip.species.scientific_name}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase()))
    .sort((a, b) => commonName(a.species, app.locale).localeCompare(commonName(b.species, app.locale), app.locale)));
  const birds=$derived([...new Map(visible.filter(c=>c.enabled).map(c=>[c.species_id,c.species])).values()]);
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
  const saveSet = createTask(async () => {
    const result = await api<{ id: number }>('/quiz/sets', { name: setName, clip_ids: selected });
    await refresh(); setId = String(result.id); setName = ''; notify('quizSetSaved');
  }, { toast: true });
  onMount(() => { void (async () => {
    try { await refresh(); if (library) { setId = String(library.sets.find(s=>s.bundled_key)?.id??''); chooseSet(); } if(context.species){const target=library?.clips.find(c=>c.enabled&&c.species_id===context.species);if(target){const others=(library?.clips??[]).filter(c=>c.enabled&&c.species_id!==context.species);selected=[target.id,...others.filter((c,i)=>others.findIndex(x=>x.species_id===c.species_id)===i).slice(0,3).map(c=>c.id)];setId='';}} }
    catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
    try{const saved=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(saved){selected=saved.selected.filter((id:number)=>library?.clips.some(c=>c.id===id&&c.enabled));setId=saved.setId;filter=saved.filter;}}catch{}
    const added=Number(new URL(page.url.hash.slice(1),page.url).searchParams.get('added'));
    if(library?.clips.some(c=>c.id===added&&c.enabled)){selected=[...new Set([...selected,added])];setId='';}
    restored=true;
  })(); });
</script>
<div class="page narrow"><div class="crumbs"><a href={`${resolve('/quiz')}${continuation}`}>{t('uBackQuiz')}</a></div>
  <h1>{t('uQuizChoose')}</h1>
  {#if loadError}<p class="error">{t(loadError)}</p>{:else if library}
    <section class="panel"><label>{t('uQuizSaved')}<select bind:value={setId} onchange={chooseSet}><option value="">{t('quizAllClips')}</option>{#each library.sets as set}<option value={String(set.id)}>{set.bundled_key?t('quizSampleSet'):set.name}</option>{/each}</select></label>
      <label>{t('quizFilter')}<input bind:value={filter}/></label>
      <div class="birds">{#each birds as bird}<label class="checkbox bird"><input type="checkbox" checked={visible.some(c=>c.species_id===bird.id&&selected.includes(c.id))} onchange={e=>{const ids=library!.clips.filter(c=>c.enabled&&c.species_id===bird.id).map(c=>c.id);selected=e.currentTarget.checked?[...new Set([...selected,...ids])]:selected.filter(id=>!ids.includes(id));setId='';}}/><span>{commonName(bird,app.locale)}<i class="scientific-name">{bird.scientific_name}</i></span></label>{/each}</div>
      <Button variant="primary" pending={start.pending} disabled={speciesCount<2} onclick={()=>start.run()}>{t('quizStart')} · {speciesCount} {t('quizSpecies')}</Button>
      {#if start.error}<p class="error">{t(start.error)}</p>{/if}
    </section>
    <form class="panel" onsubmit={e=>{e.preventDefault();saveSet.run();}}><label>{t('uQuizSelectionName')}<input bind:value={setName} required maxlength="200"/></label><Button type="submit" pending={saveSet.pending} disabled={!setName.trim()||!selected.length}>{t('uQuizSave')}</Button>
      {#if saveSet.error}<p class="error">{t(saveSet.error)}</p>{/if}</form>
  {:else}<p>{t('loading')}</p>{/if}
</div>
<style>label{margin:16px 0}.birds{display:grid;grid-template-columns:1fr 1fr;gap:0 20px}.bird{padding:16px 0;border-bottom:1px solid rgb(var(--stone-300))}</style>
