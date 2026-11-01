<script lang="ts">
  import { api } from './api.svelte';
  import { t, type Job } from './app.svelte';
  import { fill } from './workflow';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { notify } from './ui/toast.svelte';
  let { job }: { job: Job } = $props();
  const active = $derived(job.status === 'queued' || job.status === 'running');
  // Building the sound map counts steps, not recordings, and stops through the shared operation cancel.
  const explore = $derived(job.stage.startsWith('explore_'));
  const cancel = createTask(async () => { await api(explore ? '/operations/cancel' : '/jobs/cancel', {}); notify('cancelRequested', 'info'); }, { toast: true });
</script>

<div class="job">
  <div class="section-heading">
    <!-- The job counts recordings × AIs; only the AIs a recording still lacks run, so the total is not a recording count. -->
    <strong>{fill('jobLine', { status: t(job.status), progress: fill(explore ? 'eJobProgress' : 'jobProgress', { done: job.done, total: job.total }) })}</strong>
    {#if active}<Button size="sm" pending={cancel.pending} onclick={() => cancel.run()}>{t(explore ? 'operationCancel' : 'cancel')}</Button>{/if}
  </div>
  {#if active}<p class="muted">{t(job.stage)}</p><progress value={job.done} max={job.total}></progress>{/if}
  {#if job.error}<p class="error">{t(job.error)}</p>{/if}
</div>

<style>
  .job { display: flex; flex-direction: column; gap: 8px; }
</style>
