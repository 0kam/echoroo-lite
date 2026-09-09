<script lang="ts">
  import { goto } from '$app/navigation';
  import { app, t } from '$lib/app.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  // Entry point: continue with the working dataset, otherwise start at the library.
  $effect(() => {
    if (!app.connected) return;
    const known = app.datasets.some(d => d.id === app.datasetId);
    goto(known ? `/datasets/${app.datasetId}` : '/datasets', { replaceState: true });
  });
</script>

<div class="page">
  <div class="empty">
    {#if app.connectionError}
      <p class="error" role="alert">{t(app.connectionError)}</p>
      <button class="button" onclick={() => location.reload()}>{t('retryConnection')}</button>
    {:else}
      <Spinner size={20} /><p>{t('connecting')}</p>
    {/if}
  </div>
</div>
