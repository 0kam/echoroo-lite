<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { isTauri } from '@tauri-apps/api/core';
  import { chooseDirectory } from '$lib/files';
  import { api } from '$lib/api.svelte';
  import { app,t,fileName,duration,refreshCatalog,rememberDataset } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import SiteMap from '$lib/SiteMap.svelte';
  import { appHref,returnPath } from '$lib/workflow';
  import { createTask } from '$lib/ui/task.svelte';
  const query=new URL(page.url.hash.slice(1),page.url).searchParams;
  let siteId=$state(query.get('site')||''),siteName=$state(''),folder=$state('');
  let zone=$state('Asia/Tokyo'),latitude=$state<number|null>(null),longitude=$state<number|null>(null);
  let preview=$state<{count:number;seconds:number;skipped:{path:string;code:string}[]}|null>(null);
  // Adding recordings to an existing site returns to the page it was opened from (site results or site management).
  const adding=!!query.get('site'),origin=returnPath(page.url,/^\/(datasets\/\d+|sites\/\d+\/manage)(\?[^#]*)?$/);
  const draftKey=`echoroo-import-${query.get('site')||'new'}`;
  let restored=$state(false);
  onMount(()=>{try{const draft=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(draft){({siteId,siteName,folder,zone,latitude,longitude}=draft);}}catch{}restored=true;});
  $effect(()=>{if(restored)sessionStorage.setItem(draftKey,JSON.stringify({siteId,siteName,folder,zone,latitude,longitude}));});
  const selected=$derived(app.sites.find(s=>String(s.id)===siteId));
  const body=()=>({site_id:siteId?Number(siteId):null,site_name:selected?.name??siteName,timezone:selected?.timezone??zone,latitude:selected?.latitude??latitude??0,longitude:selected?.longitude??longitude??0,folder,dataset_name:fileName(folder)||siteName});
  const inspect=createTask(async()=>{preview=await api('/imports/preview',body());});
  const browse=createTask(async()=>{const picked=await chooseDirectory();if(picked){folder=picked;preview=null;}});
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  function clearFolder(){folder='';preview=null;inspect.error='';register.error='';}
  const register=createTask(async()=>{
    const added=await api<{dataset_id:number}>('/imports',body());await refreshCatalog();
    const site=app.datasets.find(d=>d.id===added.dataset_id)!.site_id;
    const id=Math.min(...app.datasets.filter(d=>d.site_id===site).map(d=>d.id));rememberDataset(id);
    restored=false;sessionStorage.removeItem(draftKey);
    await goto(resolve(`/datasets/${id}`));
  });
</script>
<div class="page import-page">
  <div class="crumbs">{#if origin}<a href={appHref(origin)}>{t(origin.startsWith('/sites/')?'mManage':'uBackResults')}</a>{:else}<a href={resolve('/datasets')}>{t('uSites')}</a>{/if}</div>
  <h1>{adding?`${selected?`${selected.name} · `:''}${t('uAddRecordings')}`:t('uNewSite')}</h1>
  <form class="stack" onsubmit={e=>{e.preventDefault();inspect.run();}} oninput={()=>preview=null}>
    {#if !adding}<section class="panel">
      <h2>{t('uSiteDetails')}</h2><p>{t('uSiteOnce')}</p>
        <label>{t('siteName')}<input bind:value={siteName} required maxlength="200" placeholder={t('uSiteExample')}/></label>
        <SiteMap bind:latitude bind:longitude editable/>
        <div class="form-grid"><label>{t('latitude')}<input type="number" step="any" min="-90" max="90" bind:value={latitude} required/></label><label>{t('longitude')}<input type="number" step="any" min="-180" max="180" bind:value={longitude} required/></label></div>
        <label>{t('uTimeZone')}<input bind:value={zone} required/></label>
    </section>{/if}
    <section class="panel"><h2>{t('uChooseFolder')}</h2><p>{t('uFolderHint')}</p>
      {#if isTauri()}<Button pending={browse.pending} onclick={()=>browse.run()}>{t('browse')}</Button>{#if folder}<p>{fileName(folder)}</p>{/if}
      {:else}<label>{t('folder')}<input bind:value={folder} required/></label>{/if}
      {#if folder}<Button onclick={clearFolder}>{t('uClearFolder')}</Button>{/if}
      {#if browse.error}<p class="error" role="alert">{t(browse.error)}</p>{/if}
      {#if !preview}<Button type="submit" variant="primary" pending={inspect.pending} disabled={!folder}>{t('uCheckFiles')}</Button>{/if}
      {#if inspect.pending||register.pending}<Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>{/if}
      {#if inspect.error}<p class="error" role="alert">{t(inspect.error)}</p>{/if}
      {#if preview}<p><strong>{preview.count} {t('uRecordingsToAdd')}</strong> · {duration(preview.seconds)}</p>
        {#if !preview.count}<p class="notice" role="status">{t('uNoSupportedAudio')}</p>{/if}
        {#if preview.skipped.length}<details open={!preview.count}><summary>{preview.skipped.length} {t('uSkippedFiles')}</summary>{#each preview.skipped as item}<p>{fileName(item.path)} — {t(item.code)}</p>{/each}</details>{/if}
        {#if preview.count}<Button variant="primary" pending={register.pending} onclick={()=>register.run()}>{t('uAddTheseFiles')}</Button>
        {:else}<Button variant="primary" onclick={clearFolder}>{t('uChooseAnotherFolder')}</Button>{/if}
        {#if register.error}<p class="error" role="alert">{t(register.error)}</p>{/if}
      {/if}
    </section>
  </form>
</div>
<style>.import-page{max-width:760px!important}label,p{margin:16px 0}.panel>:global(.button){margin-right:12px}</style>
