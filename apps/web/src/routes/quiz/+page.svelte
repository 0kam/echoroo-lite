<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app, t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import type { QuizClip, QuizLibrary, QuizSession } from '$lib/quiz';
  import { clipTime } from '$lib/AudioRegion.svelte';
  import QuizEditor from '$lib/QuizEditor.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  let library = $state<QuizLibrary | null>(null), selected = $state<number[]>([]), setId = $state(''), setName = $state('');
  let filter = $state(''), count = $state(10), includeDisabled = $state(false), loadError = $state('');
  let showEditor = $state(false), editing = $state<QuizClip | null>(null), editorKey = $state(0), toggling = $state<number | null>(null);
  const visible = $derived((library?.clips ?? []).filter(clip => (clip.enabled || includeDisabled) &&
    `${commonName(clip.species, app.locale)} ${clip.species.scientific_name}`.toLocaleLowerCase().includes(filter.toLocaleLowerCase()))
    .sort((a, b) => commonName(a.species, app.locale).localeCompare(commonName(b.species, app.locale), app.locale)));
  const speciesCount = $derived(new Set((library?.clips ?? []).filter(clip => selected.includes(clip.id) && clip.enabled).map(clip => clip.species_id)).size);
  async function refresh() {
    library = await api<QuizLibrary>('/quiz/library');
    selected = selected.filter(id => library!.clips.some(clip => clip.id === id && clip.enabled));
  }
  function chooseSet() {
    const ids = setId ? library?.sets.find(set => set.id === Number(setId))?.clip_ids ?? [] : library?.clips.map(clip => clip.id) ?? [];
    selected = ids.filter(id => library?.clips.some(clip => clip.id === id && clip.enabled));
  }
  function edit(clip: QuizClip | null) { editing = clip; editorKey++; showEditor = true; }
  const start = createTask(async () => {
    const session = await api<QuizSession>('/quiz/sessions', { clip_ids: selected, count });
    await refresh();
    await goto(resolve(`/quiz/${session.id}`));
  }, { toast: true });
  const toggle = createTask(async (clip: QuizClip) => {
    toggling = clip.id;
    try { await api(`/quiz/clips/${clip.id}/enabled`, { enabled: !clip.enabled }); await refresh(); }
    finally { toggling = null; }
  }, { toast: true });
  const saveSet = createTask(async () => {
    const result = await api<{ id: number }>('/quiz/sets', { name: setName, clip_ids: selected });
    await refresh(); setId = String(result.id); setName = ''; notify('quizSetSaved');
  }, { toast: true });
  async function added(clip: QuizClip) {
    showEditor = false; editing = null; await refresh(); selected = [...selected.filter(id => id !== clip.id), clip.id]; setId = '';
    notify('quizClipAdded');
  }
  onMount(() => { void (async () => {
    try { await refresh(); if (library?.sets.length) { setId = String(library.sets[0].id); chooseSet(); } }
    catch (e) { loadError = e instanceof Error ? e.message : 'unknown_error'; }
  })(); });
</script>

<div class="page">
  <div class="page-header"><div><h1>{t('quiz')}</h1><p>{t('quizIntro')}</p></div></div>
  <p class="notice">{t('quizPracticeHint')}</p>
  {#if loadError}<div class="panel"><p class="error" role="alert">{t(loadError)}</p></div>
  {:else if !library}<p class="muted"><Spinner /> {t('quizLoading')}</p>
  {:else}
    <div class="two-col">
      <div class="stack">
        <section class="panel">
          <h2>{t('quizChooseQuestions')}</h2>
          <div class="form-grid">
            <label>{t('quizSet')}<select bind:value={setId} disabled={start.pending} onchange={chooseSet}><option value="">{t('quizAllClips')}</option>
              {#each library.sets as set}<option value={String(set.id)}>{set.bundled_key ? t('quizSampleSet') : set.name}</option>{/each}</select></label>
            <label>{t('quizCount')}<input type="number" min="1" max="50" step="1" bind:value={count} disabled={start.pending} /></label>
          </div>
          <p class="muted selection">{t('quizSelection')}: {selected.length} {t('quizClips')} · {speciesCount} {t('quizSpecies')} · {t('quizSamplingHint')}</p>
          <div class="actions"><Button variant="primary" size="lg" pending={start.pending} disabled={speciesCount < 2 || !Number.isInteger(count) || count < 1 || count > 50} onclick={() => start.run()}>{t('quizStart')}</Button>
            <Button onclick={() => edit(null)}>＋ {t('quizAddAudio')}</Button></div>
          {#if start.error}<p class="error" role="alert">{t(start.error)}</p>{/if}
        </section>
        <section class="panel">
          <h2>{t('quizMaterials')}</h2>
          <details class="selection-list"><summary>{t('quizSelectSpecies')}</summary>
            <label>{t('quizFilter')}<input bind:value={filter} /></label>
            <div class="actions toolbar"><Button size="sm" onclick={() => selected = [...new Set([...selected, ...visible.filter(clip => clip.enabled).map(clip => clip.id)])]}>{t('quizSelectVisible')}</Button>
              <Button size="sm" onclick={() => selected = selected.filter(id => !visible.some(clip => clip.id === id))}>{t('quizClearVisible')}</Button>
              <label class="checkbox"><input type="checkbox" bind:checked={includeDisabled} />{t('quizIncludeDisabled')}</label></div>
            <div class="clip-list">
              {#each visible as clip (clip.id)}<div class="clip-row"><label class="checkbox"><input type="checkbox" value={clip.id} bind:group={selected} disabled={!clip.enabled} />
                <span>{commonName(clip.species, app.locale)}<i class="scientific-name">{clip.species.scientific_name}</i><span class="muted">#{clip.id} · {t(`quizSound_${clip.metadata.sound_type}`)} · {clipTime(clip.seconds)}</span></span></label>
                <div class="actions"><Button size="sm" onclick={() => edit(clip)}>{t('quizListenAndSelect')}</Button>
                  <Button size="sm" pending={toggling === clip.id} disabled={toggle.pending} onclick={() => toggle.run(clip)}>{t(clip.enabled ? 'quizDisable' : 'quizEnable')}</Button></div></div>{/each}
            </div>
            <form class="save-set" onsubmit={event => { event.preventDefault(); saveSet.run(); }}><label>{t('quizSetName')}<input bind:value={setName} required maxlength="200" /></label>
              <Button type="submit" pending={saveSet.pending} disabled={!setName.trim() || !selected.length}>{t('quizSaveSet')}</Button></form>
            {#if toggle.error || saveSet.error}<p class="error" role="alert">{t(toggle.error || saveSet.error)}</p>{/if}
          </details>
          {#if showEditor}{#key editorKey}<QuizEditor initial={editing} onadded={added} />{/key}
            <Button size="sm" onclick={() => showEditor = false}>{t('quizCloseEditor')}</Button>{/if}
        </section>
      </div>
      <aside class="panel">
        <h2>{t('quizHistory')}</h2>
        {#if !library.sessions.length}<p class="empty">{t('quizChooseSession')}</p>{/if}
        <div class="list" role="list">
          {#each library.sessions as session (session.id)}<div class="list-item" role="listitem"><span>#{session.id} · {session.answered}/{session.count} · {t(session.answered === session.count ? 'quizCompleted' : 'quizResume')}</span>
            <Button size="sm" href={resolve(`/quiz/${session.id}`)}>{t(session.answered === session.count ? 'open' : 'quizResume')} ›</Button></div>{/each}
        </div>
      </aside>
    </div>
  {/if}
</div>

<style>
  .selection { margin: 12px 0; }
  .selection-list summary { margin-bottom: 14px; }
  .toolbar { margin: 12px 0; }
  .clip-list { max-height: 440px; overflow: auto; border-block: 1px solid rgb(var(--stone-300)); }
  .clip-row { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 0; border-bottom: 1px solid rgb(var(--stone-200)); }
  .save-set { display: flex; align-items: flex-end; gap: 12px; margin-top: 16px; }
  .save-set label { flex: 1; }
</style>
