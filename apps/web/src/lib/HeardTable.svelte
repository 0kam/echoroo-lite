<script lang="ts">
  import { isTauri } from '@tauri-apps/api/core';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { chooseDirectory, chooseFile } from './files';
  import Button from './ui/Button.svelte';
  import { createTask } from './ui/task.svelte';
  import { fill } from './workflow';
  // The answers given by ear as a table (CSV): out to a folder, or in from a file on a PC that has the same recordings.
  type Result = { added: number; different: number; same: number; no_recording: number; no_species: number; invalid: number };
  let { datasetId, onchange }: { datasetId: number; onchange: () => void | Promise<void> } = $props();
  let folder = $state(''), file = $state(''), saved = $state<{ path: string; rows: number } | null>(null), result = $state<Result | null>(null);
  const out = createTask(async () => { saved = null;
    if (isTauri()) { const path = await chooseDirectory(); if (!path) return; folder = path; }
    saved = await api<{ path: string; rows: number }>(`/datasets/${datasetId}/heard/export`, { folder }); });
  const take = createTask(async () => { result = null;
    if (isTauri()) { const path = await chooseFile(t('shTableKind'), ['csv']); if (!path) return; file = path; }
    result = await api<Result>(`/datasets/${datasetId}/heard/import`, { dataset_id: datasetId, path: file }); await onchange(); });
  const left = $derived(result ? result.no_recording + result.no_species + result.invalid : 0);
</script>
<details class="panel table"><summary>{t('shTable')}</summary>
  <p class="muted">{t('shTableNote')}</p>
  {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} disabled={out.pending} /></label>{/if}
  <div class="actions"><Button pending={out.pending} disabled={!isTauri() && !folder} onclick={() => out.run()}>{t('shTableOut')}</Button></div>
  {#if saved}<p class="info" role="status">{fill('shTableSaved', { n: saved.rows, path: saved.path })}</p>{/if}
  {#if out.error}<p class="error" role="alert">{t(out.error)}</p>{/if}
  {#if !isTauri()}<label>{t('shFilePath')}<input bind:value={file} disabled={take.pending} /></label>{/if}
  <div class="actions"><Button pending={take.pending} disabled={!isTauri() && !file} onclick={() => take.run()}>{t('shTableIn')}</Button></div>
  {#if result}<p class="info" role="status">{fill('shTableTaken', { added: result.added, same: result.same })}{#if result.different}{' '}{fill('shTableDifferent', { n: result.different })}{/if}{#if left}{' '}{fill('shTableLeft', { n: left, recording: result.no_recording, species: result.no_species, invalid: result.invalid })}{/if}</p>{/if}
  {#if take.error}<p class="error" role="alert">{t(take.error)}</p>{/if}
</details>
<style>.table p{margin:12px 0}summary{min-height:44px;padding:10px 0;cursor:pointer}.table input{display:block;width:100%;max-width:560px}.table .actions{margin:8px 0}</style>
