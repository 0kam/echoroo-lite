<script lang="ts" module>
  // Progress observations per job id; kept across pages so the estimate survives navigation.
  const seen = new Map<number, { start: number; startDone: number; done: number; at: number; left: number | null }>();
</script>

<script lang="ts">
  import { api } from './api.svelte';
  import { t, type Job } from './app.svelte';
  import { fill, roughTime } from './workflow';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { notify } from './ui/toast.svelte';
  let { job }: { job: Job } = $props();
  const active = $derived(job.status === 'queued' || job.status === 'running');
  // Remaining time from the observed rate, re-estimated when a pair finishes and counted down in between;
  // shown once 10 s of progress have been seen, hidden while results are prepared.
  const left = $derived.by(() => {
    if (job.status !== 'running' || !job.done || job.done >= job.total) return null;
    const now = performance.now(), p = seen.get(job.id);
    if (!p) { seen.set(job.id, { start: now, startDone: job.done, done: job.done, at: now, left: null }); return null; }
    if (job.done !== p.done) {
      p.done = job.done; p.at = now;
      p.left = now - p.start >= 10000 && job.done > p.startDone ? (now - p.start) / (job.done - p.startDone) * (job.total - job.done) : null;
    }
    return p.left === null ? null : Math.max(p.left - (now - p.at), 5000) / 1000;
  });
  const cancel = createTask(async () => { await api('/jobs/cancel', {}); notify('cancelRequested', 'info'); }, { toast: true });
</script>

<div class="job">
  <div class="section-heading">
    <!-- The job counts recordings × AIs; only the AIs a recording still lacks run, so the total is not a recording count. -->
    <strong>{fill('jobLine', { status: t(job.status), progress: fill('jobProgress', { done: job.done, total: job.total }) })}</strong>
    {#if active}<Button size="sm" pending={cancel.pending} onclick={() => cancel.run()}>{t('cancel')}</Button>{/if}
  </div>
  {#if active}<p class="muted">{t(job.stage)}{#if left !== null}{' · '}{fill('jobTimeLeft', { time: roughTime(left) })}{/if}</p><progress value={job.done} max={job.total}></progress>{/if}
  {#if job.error}<p class="error">{t(job.error)}</p>{/if}
</div>

<style>
  .job { display: flex; flex-direction: column; gap: 8px; }
</style>
