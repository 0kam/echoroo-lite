<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { chooseDirectory } from '$lib/files';
  import { api } from '$lib/api.svelte';
  import { app,t,dayRange,duration } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { activityText,fill,presenceText,rangeQuery,type Overview } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Export = {id:number;path:string;folder_path?:string;report_path?:string;format?:string;created_at:string;results?:{report_id:number}[]};
  type Snapshot = {id:number;created_at:string;species:Species|null;start_date:string|null;end_date:string|null};
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  let overview=$state<Overview|null>(null),selected=$state<string[]>([]),error=$state(''),folder=$state('');
  let output=$state<Export|null>(null),history=$state<Export[]>([]),earlier=$state<Snapshot[]>([]);
  const draftKey=`echoroo-export-${id}-${query}`;
  let restored=$state(false);
  $effect(()=>{if(restored)sessionStorage.setItem(draftKey,JSON.stringify({selected,folder}));});
  const back=$derived(`${resolve(`/datasets/${id}`)}?${overview?rangeQuery(overview.start_date,overview.end_date):query}`);
  onMount(()=>{void(async()=>{try{
    const [data,saved,snapshots]=await Promise.all([api<Overview>(`/datasets/${id}/overview?${query}`),api<Export[]>(`/datasets/${id}/exports`),api<Snapshot[]>(`/reports?dataset_id=${id}`)]);
    // A3: only species a person confirmed by listening are included unless chosen.
    overview=data;history=saved;selected=data.species.filter(row=>row.presence.status==='confirmed').map(row=>row.species.id);
    // Snapshots saved by an export are opened from that export; list only the ones saved on their own by earlier versions.
    const exported=new Set(saved.flatMap(item=>item.results?.map(result=>result.report_id)??[]));earlier=snapshots.filter(item=>!exported.has(item.id));
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
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:overview?.site.timezone}).format(new Date(value.replace(' ','T')+'Z'));
  const openReport=createTask(async(exportId:number,kind:'document'|'folder')=>{
    await api(`/workflow-exports/${exportId}/open?kind=${kind}`,{});
  });
</script>
<div class="page narrow report-page">
  <div class="crumbs"><a href={back}>{t('uBackResults')}</a></div>
  <h1>{t('wExport')}</h1><p class="lead">{t('rExportIntro')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if overview}
    <section class="panel">
      <h2>{overview.site.name}</h2>
      <p>{dayRange(overview.start_date,overview.end_date)} · {overview.site.timezone}</p>
      <p>{fill('rRecordingsCount',{n:overview.records.toLocaleString(app.locale)})} · {fill('wTotalLength',{duration:duration(overview.recorded_seconds)})}</p>
      <h3>{t('wIncludeSpecies')} · {fill('wSpeciesCount',{n:selected.length})}</h3>
      {#each [['confirmed','rGroupConfirmed'],['not_found','rGroupMissed'],['unchecked','rGroupAi']] as [status,title]}{@const rows=overview.species.filter(row=>row.presence.status===status)}
        {#if rows.length}<details open={status==='confirmed'}><summary>{t(title)} · {fill('wSpeciesCount',{n:rows.length})}</summary>
          {#if status==='unchecked'}<p class="notice">{t('rAiOnlyCaution')}</p>{/if}
          {#each rows as row}<label class="export-species"><input type="checkbox" bind:group={selected} value={row.species.id} disabled={exportFiles.pending}/>
            <span>{commonName(row.species,app.locale)}<i class="scientific-name">{row.species.scientific_name}</i>
              <span class="muted">{presenceText(row.presence)} · {t('wActivity')}: {activityText(row.models)}</span></span></label>{/each}
        </details>{/if}{/each}
      {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} disabled={exportFiles.pending}/></label>{/if}
      <!-- Same shape as adding recordings: a status box that is always there, then one fixed main button. -->
      <div class="status-box" role="status" aria-live="polite">
        {#if exportFiles.pending}<p class="busy"><Spinner/> {t('rPreparing')}</p><Button size="sm" pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>
        {:else if exportFiles.error}<p class="error">{t(exportFiles.error)}</p>
        {:else if output}<p><strong>{t('rCreated')}</strong> {t('rReady')}</p>
          <div class="actions"><Button pending={openReport.pending} onclick={()=>openReport.run(output!.id,'document')}>{t('rOpen')}</Button>
            <Button pending={openReport.pending} onclick={()=>openReport.run(output!.id,'folder')}>{t('rOpenFolder')}</Button></div>
          <p class="path">{output.folder_path}</p>
        {:else}<p>{t('rExportContents')}</p>{/if}
        {#if openReport.error}<p class="error" role="alert">{t(openReport.error)}</p>{/if}
      </div>
      <Button variant={output?'default':'primary'} pending={exportFiles.pending} disabled={!selected.length||(!isTauri()&&!folder)} aria-describedby="export-reason" onclick={()=>exportFiles.run()}>{t('rCreate')}</Button>
      <p class="muted" id="export-reason">{!selected.length?t('rSaveIdle'):!isTauri()&&!folder?t('rNeedFolder'):''}</p>
    </section>
    {#if history.length}<section class="panel"><h2>{t('wExportHistory')}</h2>{#each history as item}<div class="saved">
      <div><p>{when(item.created_at)}</p>
        <small class="path">{item.folder_path??item.path}</small></div>
      {#if item.folder_path}<Button pending={openReport.pending} onclick={()=>openReport.run(item.id,'document')}>{t('rOpen')}</Button>{/if}
    </div>{/each}</section>{/if}
    {#if earlier.length}<details><summary>{t('wEarlierReports')} · {earlier.length}</summary>{#each earlier as item}<p><a href={`${resolve(`/datasets/${id}/reports/${item.id}`)}?${query}`}>{item.species?(item.species.common_names&&commonName(item.species,app.locale))||item.species.scientific_name:t('species')} · {item.start_date&&item.end_date?dayRange(item.start_date,item.end_date):''} · {when(item.created_at)}</a></p>{/each}</details>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('rLoading')}</p>{/if}
</div>
<style>h2{margin-bottom:12px}section>p,section>label{margin:16px 0}.lead{font-size:18px}.busy{display:flex;align-items:center;gap:8px}
  .export-species{display:flex;align-items:center;gap:12px;padding:16px 0;border-bottom:1px solid rgb(var(--stone-300))}details{margin:8px 0}summary{font-size:18px;padding:12px 0;cursor:pointer}h3{margin:24px 0 0}.export-species .muted{display:block}
  .path{overflow-wrap:anywhere;color:rgb(var(--stone-700));font-size:14px}.saved{display:flex;align-items:center;gap:24px;justify-content:space-between;padding:16px 0;border-top:1px solid rgb(var(--stone-300))}.saved p{margin:0 0 8px}</style>
