<script lang="ts">
  import { api } from './api.svelte';
  import { app, t, type Job } from './app.svelte';
  import { fill } from './workflow';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { notify } from './ui/toast.svelte';
  let { job }: { job: Job } = $props();
  const active = $derived(job.status === 'queued' || job.status === 'running');
  // Each recording is analysed once per AI, so the job counts recordings × AIs.
  const models = $derived(app.birdnet ? 2 : 1);
  const cancel = createTask(async () => { await api('/jobs/cancel', {}); notify('cancelRequested', 'info'); }, { toast: true });
</script>

<div class="job">
  <div class="section-heading">
    <strong>{fill('jobLine', { status: t(job.status), progress: fill('jobProgress', { done: job.done, total: job.total }),
      models: fill(models === 2 ? 'jobModelsBoth' : 'jobModelsOne', { n: Math.round(job.total / models) }) })}</strong>
    {#if active}<Button size="sm" pending={cancel.pending} onclick={() => cancel.run()}>{t('cancel')}</Button>{/if}
  </div>
  {#if active}<p class="muted">{t(job.stage)}</p><progress value={job.done} max={job.total}></progress>{/if}
  {#if job.error}<p class="error">{t(job.error)}</p>{/if}
</div>

<style>
  .job { display: flex; flex-direction: column; gap: 8px; }
</style>
