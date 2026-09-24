<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { chooseDirectory } from '$lib/files';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { rangeQuery,type Overview } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Export = {id:number;path:string;folder_path?:string;report_path?:string;format?:string;created_at:string};
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  let overview=$state<Overview|null>(null),selected=$state<string[]>([]),error=$state(''),folder=$state('');
  let output=$state<Export|null>(null),history=$state<Export[]>([]);
  const draftKey=`echoroo-export-${id}-${query}`;
  let restored=$state(false);
  $effect(()=>{if(restored)sessionStorage.setItem(draftKey,JSON.stringify({selected,folder}));});
  const back=$derived(`${resolve(`/datasets/${id}`)}?${overview?rangeQuery(overview.start_date,overview.end_date):query}`);
  onMount(()=>{void(async()=>{try{
    const [data,saved]=await Promise.all([api<Overview>(`/datasets/${id}/overview?${query}`),api<Export[]>(`/datasets/${id}/exports`)]);
    overview=data;history=saved;selected=data.species.map(row=>row.species.id);
    try{const draft=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(draft){selected=draft.selected.filter((id:string)=>data.species.some(row=>row.species.id===id));folder=draft.folder;}}catch{}
    restored=true;
  }catch(e){error=e instanceof Error?e.message:'unknown_error';}})();});
  const exportFiles=createTask(async()=>{
    if(!overview)return;
    if(isTauri()){const path=await chooseDirectory();if(!path)return;folder=path;}
    output=null;
    output=await api<Export>('/workflow-exports',{dataset_id:id,start_date:overview.start_date,end_date:overview.end_date,
      revision:overview.revision,species_ids:selected,folder,locale:app.locale});
    history=await api(`/datasets/${id}/exports`);
  });
  const openReport=createTask(async(exportId:number,kind:'document'|'folder')=>{
    await api(`/workflow-exports/${exportId}/open?kind=${kind}`,{});
  });
</script>
<div class="page report-page">
  <div class="crumbs"><a href={back}>{t('uBackResults')}</a><span>{overview?.site.name}</span></div>
  <h1>{t('wExport')}</h1><p class="lead">{t('rExportIntro')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if overview}
    <section class="panel">
      <h2>{overview.site.name}</h2>
      <p>{overview.start_date}–{overview.end_date} · {overview.site.timezone}</p>
      <p>{overview.records.toLocaleString(app.locale)} {t('rAudioFiles')} · {(overview.recorded_seconds/3600).toLocaleString(app.locale,{maximumFractionDigits:1})} {t('rHours')}</p>
      <details><summary>{t('wIncludeSpecies')} · {selected.length}</summary>
        {#each overview.species as row}<label class="export-species"><input type="checkbox" bind:group={selected} value={row.species.id} disabled={exportFiles.pending}/>
          <span>{commonName(row.species,app.locale)}<i class="scientific-name">{row.species.scientific_name}</i></span></label>{/each}
      </details>
      <p>{t('rExportContents')}</p>
      {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} disabled={exportFiles.pending}/></label>{/if}
      <Button variant="primary" pending={exportFiles.pending} disabled={!selected.length||(!isTauri()&&!folder)} onclick={()=>exportFiles.run()}>{t('rCreate')}</Button>
      {#if exportFiles.pending}<p role="status"><Spinner/> {t('rPreparing')}</p>{/if}
      {#if exportFiles.error}<p class="error" role="alert">{t(exportFiles.error)}</p>{/if}
    </section>
    {#if output}<section class="panel output" aria-live="polite"><h2>{t('rCreated')}</h2><p>{t('rReady')}</p>
      <div class="actions"><Button variant="primary" pending={openReport.pending} onclick={()=>openReport.run(output!.id,'document')}>{t('rOpen')}</Button>
        <Button pending={openReport.pending} onclick={()=>openReport.run(output!.id,'folder')}>{t('rOpenFolder')}</Button></div>
      <p class="path">{output.folder_path}</p>
    </section>{/if}
    {#if openReport.error}<p class="error" role="alert">{t(openReport.error)}</p>{/if}
    {#if history.length}<section class="panel"><h2>{t('wExportHistory')}</h2>{#each history as item}<div class="saved">
      <div><p>{new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:overview.site.timezone}).format(new Date(item.created_at.replace(' ','T')+'Z'))}</p>
        <small class="path">{item.folder_path??item.path}</small></div>
      {#if item.folder_path}<Button pending={openReport.pending} onclick={()=>openReport.run(item.id,'document')}>{t('rOpen')}</Button>{/if}
    </div>{/each}</section>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.report-page{max-width:960px}h1{margin-top:20px}h2{margin-bottom:12px}p{margin:16px 0}.lead{font-size:18px}.export-species{display:flex;align-items:center;gap:14px;padding:16px 0;border-bottom:1px solid rgb(var(--stone-300))}.export-species input{width:22px;height:22px}details{margin:24px 0}summary{font-size:18px;padding:12px 0;cursor:pointer}.path{overflow-wrap:anywhere;color:rgb(var(--stone-700));font-size:14px}.saved{display:flex;align-items:center;gap:24px;justify-content:space-between;padding:18px 0;border-top:1px solid rgb(var(--stone-300))}.saved p{margin:0 0 8px}.output{border-color:rgb(var(--primary-700))}</style>
