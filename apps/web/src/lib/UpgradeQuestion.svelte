<script lang="ts">
  // Results analysed before 0.3 kept only some classes (BirdNET 2.4). Asked once as a dialog; later offered in Settings.
  import { api } from './api.svelte';
  import { app,t,refreshCatalog } from './app.svelte';
  import { analysisSeconds,fill,modelName,roughTime } from './workflow';
  import Button from './ui/Button.svelte';
  import { notify } from './ui/toast.svelte';
  import { createTask } from './ui/task.svelte';
  type Entry={engine:string;answer:'redo'|'keep'|null;sites:number;recordings:number;seconds:number};
  // `settings`: the line under the AI choice, for results the user chose to keep for now.
  let { settings=false }:{ settings?:boolean }=$props();
  let entries=$state<Entry[]>([]),dialog=$state<HTMLDialogElement>();
  const asked=$derived(entries.find(entry=>entry.answer===null));
  // Results kept for now, and results whose analysis was started again but has not finished (stopped, failed, or waiting).
  const kept=$derived(entries.filter(entry=>entry.answer!==null));
  const busy=$derived(app.jobs.some(job=>job.status==='queued'||job.status==='running'));
  async function load(){try{entries=await api<Entry[]>('/analysis-upgrade');}catch{/* Without an answer nothing is asked. */}}
  // Asked again after recordings are added or removed and after each analysis ends.
  $effect(()=>{void app.recordingsVersion;void app.jobs.map(job=>`${job.id}:${job.status}`).join(',');void load();});
  $effect(()=>{if(!settings&&asked&&dialog&&!dialog.open)dialog.showModal();});
  const estimate=(entry:Entry)=>{const seconds=analysisSeconds([{engine:entry.engine,recordings:entry.recordings,seconds:entry.seconds}]);return seconds==null?t('upTimeUnknown'):roughTime(seconds);};
  const reply=createTask(async(entry:Entry,answer:'redo'|'keep')=>{
    const result=await api<{jobs:number[]}>('/analysis-upgrade',{engine:entry.engine,answer});
    dialog?.close();await load();
    if(answer==='redo'){await refreshCatalog();notify(result.jobs.length?'upStarted':'upWaiting',result.jobs.length?'success':'info');}
  },{toast:true});
</script>
{#if settings}{#each kept as entry (entry.engine)}<div class="notice"><p>{fill(entry.answer==='redo'?'upSettingsRedo':'upSettings',{model:modelName(entry.engine),sites:entry.sites,n:entry.recordings})}</p>
  <p class="muted">{t('upTime')}: {estimate(entry)}　{t('upKeep')}</p>
  <Button pending={reply.pending} disabled={busy} onclick={()=>reply.run(entry,'redo')}>{t('upRedo')}</Button></div>{/each}
{:else}<dialog bind:this={dialog} oncancel={event=>event.preventDefault()} aria-labelledby="upgrade-title">
  {#if asked}<h2 id="upgrade-title">{t('upTitle')}</h2>
    <p>{fill('upBody',{model:modelName(asked.engine)})}</p>
    <dl><div><dt>{t('upRecordings')}</dt><dd>{fill('upRecordingsValue',{sites:asked.sites,n:asked.recordings})}</dd></div>
      <div><dt>{t('upTime')}</dt><dd>{estimate(asked)}</dd></div></dl>
    <p>{t('upKeep')}</p>
    <div class="actions"><Button variant="primary" pending={reply.pending} onclick={()=>reply.run(asked,'redo')}>{t('upRedo')}</Button>
      <Button disabled={reply.pending} onclick={()=>reply.run(asked,'keep')}>{t('upLater')}</Button></div>
    <p class="muted">{t('upNote')}</p>{/if}
</dialog>{/if}
<style>dialog{max-width:560px;width:calc(100% - 32px);padding:24px;border:1px solid #ccc;border-radius:12px;background:rgb(var(--stone-50));color:inherit}dialog::backdrop{background:#0006}
  dl>div{display:flex;justify-content:space-between;gap:16px;padding:5px 0}h2{margin-top:0}p{margin:12px 0}.notice p:first-child{margin-top:0}</style>
