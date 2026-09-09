<script lang="ts">
  import { api } from './api.svelte';
  import { t, datasetOf, type Job } from './app.svelte';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { notify } from './ui/toast.svelte';
  let { job, showDataset = false }: { job: Job; showDataset?: boolean } = $props();
  const active = $derived(job.status === 'queued' || job.status === 'running');
  const cancel = createTask(async () => { await api('/jobs/cancel', {}); notify('cancelRequested', 'info'); }, { toast: true });
</script>

<div class="job" class:active>
  <div class="section-heading">
    <strong>{t(job.status)}{#if showDataset} · {datasetOf(job.dataset_id)?.name ?? ''}{/if}</strong>
    {#if active}<Button size="sm" pending={cancel.pending} onclick={() => cancel.run()}>{t('cancel')}</Button>{/if}
  </div>
  <p class="muted">{t(job.stage)} · {job.done} / {job.total}{#if job.total} · {Math.round(job.done / job.total * 100)}% {t('percentDone')}{/if}</p>
  {#if active}<progress value={job.done} max={job.total}></progress>{/if}
  {#if job.error}<p class="error">{t(job.error)}</p>{/if}
</div>

<style>
  .job { display: flex; flex-direction: column; gap: 6px; padding: 10px 0; border-bottom: 1px solid rgb(var(--stone-200)); font-size: 13px; }
  .job:last-child { border-bottom: 0; }
  .job.active { padding: 12px; border: 1px solid rgb(var(--color-success)); border-radius: 6px; background: rgb(var(--color-success-light)); }
</style>
