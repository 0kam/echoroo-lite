<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  let configured = $state(false), key = $state(''), keySaved = $state(false);
  const save = createTask(async () => {
    configured = (await api<{ configured: boolean }>('/xeno-canto/key', { key: key.trim() })).configured;
    key = ''; keySaved = true;
  });
  onMount(() => { void api<{ configured: boolean }>('/xeno-canto/key').then(r => configured = r.configured).catch(e => save.error = e.message); });
</script>

<p class="muted">{t('xcKeyHint')} <a href="https://xeno-canto.org/account" target="_blank" rel="noreferrer">Xeno-Canto</a></p>
<p class="small-text"><strong>{t(configured ? 'xcConfigured' : 'xcNotConfigured')}</strong></p>
<form class="inline" onsubmit={e => { e.preventDefault(); save.run(); }}>
  <label>{t('xcKey')}<input type="password" autocomplete="off" bind:value={key} maxlength="500" /></label>
  <Button type="submit" pending={save.pending}>{t('xcSaveKey')}</Button>
</form>
{#if keySaved}<p class="saved" role="status">{t('xcKeySaved')}</p>{/if}
{#if save.error}<p class="error" role="alert">{t(save.error)}</p>{/if}

<style>
  form { margin-top: 8px; }
  form :global(.button) { margin-bottom: 1px; }
</style>
