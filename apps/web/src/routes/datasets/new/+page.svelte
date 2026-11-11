<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount, untrack } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { isTauri } from '@tauri-apps/api/core';
  import { chooseDirectory } from '$lib/files';
  import { api } from '$lib/api.svelte';
  import { app,t,fileName,duration,refreshCatalog,rememberDataset } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import SiteMap from '$lib/SiteMap.svelte';
  import { analysisSeconds,appHref,fill,focusMissing,missingText,returnPath,roughTime } from '$lib/workflow';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  type Preview={count:number;seconds:number;first:string|null;last:string|null;skipped:{path:string;code:string}[]};
  const query=new URL(page.url.hash.slice(1),page.url).searchParams;
  const pcZone=Intl.DateTimeFormat().resolvedOptions().timeZone||'UTC';
  const zones=Intl.supportedValuesOf('timeZone');
  let siteId=$state(query.get('site')||''),siteName=$state(''),folder=$state(''),folderDraft=$state('');
  let zone=$state(pcZone),latitude=$state<number|null>(null),longitude=$state<number|null>(null);
  // Adding recordings to an existing site returns to the page it was opened from (site results or site management).
  const adding=!!query.get('site'),origin=returnPath(page.url,/^\/(datasets\/\d+|sites\/\d+\/manage)(\?[^#]*)?$/);
  const draftKey=`echoroo-import-${query.get('site')||'new'}`;
  let restored=$state(false);
  onMount(()=>{try{const draft=JSON.parse(sessionStorage.getItem(draftKey)||'null');if(draft){({siteId,siteName,folder,zone,latitude,longitude}=draft);folderDraft=folder;}}catch{}restored=true;});
  $effect(()=>{if(restored)sessionStorage.setItem(draftKey,JSON.stringify({siteId,siteName,folder,zone,latitude,longitude}));});
  const selected=$derived(app.sites.find(s=>String(s.id)===siteId));
  const scanZone=$derived(selected?.timezone??zone);
  const body=()=>({site_id:siteId?Number(siteId):null,site_name:selected?.name??siteName,timezone:scanZone,latitude:selected?.latitude??latitude??0,longitude:selected?.longitude??longitude??0,folder,dataset_name:fileName(folder)||siteName});

  // The folder is checked as soon as it is chosen (and again if the time zone changes); there is no separate check button.
  let preview=$state<Preview|null>(null),scanning=$state(false),scanError=$state(''),scanVersion=0;
  async function scan(){
    const version=++scanVersion;preview=null;scanError='';register.error='';
    if(!folder){scanning=false;return;}
    if(scanning)await api('/operations/cancel',{}).catch(()=>{});
    scanning=true;
    try{const result=await api<Preview>('/imports/preview',{folder,site_id:body().site_id,timezone:scanZone});if(version===scanVersion)preview=result;}
    catch(e){if(version===scanVersion)scanError=e instanceof Error?e.message:'unknown_error';}
    finally{if(version===scanVersion)scanning=false;}
  }
  $effect(()=>{if(!restored)return;void folder;void scanZone;untrack(()=>void scan());});
  // Choosing the same folder again re-reads it (e.g. after renaming files); the effect only runs when the value changes.
  const setFolder=(value:string)=>{if(value!==folder)folder=value;else if(!scanning)void scan();};
  const browse=createTask(async()=>{const picked=await chooseDirectory();if(picked)setFolder(picked);});
  const commitFolder=()=>setFolder(folderDraft.trim());
  const filenameExamples=['2MA04037_20240808_060000.wav','20240808T060000.wav','REC_2024-08-08_06-00-00_001.wav'];
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const register=createTask(async()=>{
    const added=await api<{dataset_id:number;count:number}>('/imports',body());await refreshCatalog();
    const site=app.datasets.find(d=>d.id===added.dataset_id)!.site_id;
    const id=Math.min(...app.datasets.filter(d=>d.site_id===site).map(d=>d.id));rememberDataset(id);
    restored=false;sessionStorage.removeItem(draftKey);
    await goto(resolve(`/datasets/${id}`));
    notify('uRecordingsAdded','success',undefined,{n:added.count.toLocaleString(app.locale)});
  });
  // Skipped files are summarised by reason; the file names stay in a short list underneath.
  const reasons=$derived([...(preview?.skipped??[]).reduce((counts,item)=>counts.set(item.code,(counts.get(item.code)??0)+1),new Map<string,number>())]);
  const allRegistered=$derived(!!preview&&!preview.count&&preview.skipped.length>0&&preview.skipped.every(item=>item.code==='duplicate'));
  // Missing input keeps the button enabled; a press marks each missing field. Waiting on the folder check disables it.
  let tried=$state(false);
  const noName=$derived(!adding&&!siteName.trim()),noLatitude=$derived(!adding&&!(latitude!==null&&Math.abs(latitude)<=90)),noLongitude=$derived(!adding&&!(longitude!==null&&Math.abs(longitude)<=180));
  const missing=$derived(missingText([noName&&'siteName',(noLatitude||noLongitude)&&'uMapPlace',!folder&&'uChooseFolder']));
  const blocked=$derived(scanning?'uScanWait':scanError?'uScanRetry':!preview?.count?'uNothingToAdd':'');
  // Every AI in use analyses every new recording; nothing is shown until each has a speed measurement.
  const estimate=$derived(preview?.count?analysisSeconds(app.ai.models.filter(m=>m.enabled&&m.available).map(m=>({engine:m.id,recordings:preview!.count,seconds:preview!.seconds}))):null);
  const localTime=(iso:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:scanZone}).format(new Date(iso));
</script>
<div class="page narrow">
  <div class="crumbs">{#if origin}<a href={appHref(origin)}>{t(origin.startsWith('/sites/')?'mBackManage':'uBackResults')}</a>{:else}<a href={resolve('/datasets')}>{t('uBackSites')}</a>{/if}</div>
  <h1>{adding?fill('uAddRecordingsTo',{site:selected?.name??''}):t('uNewSite')}</h1>
  <div class="steps">
    {#if adding}<section class="panel step-card"><h2>{fill('uSiteSame',{site:selected?.name??''})}</h2></section>
    {:else}<section class="panel step-card stack">
      <h2>{t('uSiteDetails')}</h2><p class="muted">{t('uSiteOnce')}</p>
      <label>{t('siteName')}<input bind:value={siteName} required maxlength="200" placeholder={t('uSiteExample')} disabled={register.pending} aria-invalid={tried&&noName||undefined} aria-describedby={tried&&noName?'name-error':undefined}/></label>
      {#if tried&&noName}<p class="field-error" id="name-error">{t('uEnterSiteName')}</p>{/if}
      <SiteMap bind:latitude bind:longitude editable={!register.pending}/>
      <div class="form-grid"><label>{t('latitude')}<input type="number" step="any" min="-90" max="90" bind:value={latitude} required disabled={register.pending} aria-invalid={tried&&noLatitude||undefined} aria-describedby={tried&&noLatitude?'place-error':undefined}/></label><label>{t('longitude')}<input type="number" step="any" min="-180" max="180" bind:value={longitude} required disabled={register.pending} aria-invalid={tried&&noLongitude||undefined} aria-describedby={tried&&noLongitude?'place-error':undefined}/></label></div>
      {#if tried&&(noLatitude||noLongitude)}<p class="field-error" id="place-error">{t('uEnterPlace')}</p>{/if}
      <label>{t('uTimeZone')}<input list="time-zones" value={zone} onchange={e=>zone=e.currentTarget.value.trim()} required autocomplete="off" disabled={register.pending}/></label>
      <datalist id="time-zones">{#each zones as name}<option value={name}></option>{/each}</datalist>
      <p class="muted">{t('uTimeZoneHint')}</p>
    </section>{/if}
    <section class="panel step-card stack"><h2>{t('uChooseFolder')}</h2><p class="muted">{t('uFolderHint')}</p>
      <div class="muted"><p>{t('uFilenameFormat')}</p><ul>{#each filenameExamples as name}<li><code>{name}</code></li>{/each}</ul><p>{fill('uFilenameZone',{zone:scanZone})}</p></div>
      {#if isTauri()}
        {#if folder}<p>{fill('uChosenFolder',{name:fileName(folder)})} <Button class="link" disabled={register.pending} onclick={()=>browse.run()}>{t('uChooseAnotherFolder')}</Button></p>
        {:else}<Button pending={browse.pending} aria-invalid={tried||undefined} aria-describedby={tried?'folder-error':undefined} onclick={()=>browse.run()}>{t('browse')}</Button>{/if}
      {:else}<label>{t('folder')}<input bind:value={folderDraft} onchange={commitFolder} onkeydown={e=>{if(e.key==='Enter'){e.preventDefault();commitFolder();}}} disabled={register.pending} aria-invalid={tried&&!folder||undefined} aria-describedby={tried&&!folder?'folder-error':undefined}/></label>{/if}
      {#if tried&&!folder}<p class="field-error" id="folder-error">{t('uPickFolder')}</p>{/if}
      {#if browse.error}<p class="error" role="alert">{t(browse.error)}</p>{/if}
    </section>
    <section class="panel step-card stack"><h2>{t('uCheckAndAdd')}</h2>
      <div class="status-box" role="status" aria-live="polite">
        {#if register.pending}<p class="busy"><Spinner/> {t('uAdding')}</p><Button size="sm" pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>
        {:else if scanning}<p class="busy"><Spinner/> {t('uScanning')}</p><Button size="sm" pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>
        {:else if scanError}<p class="error">{t(scanError)}</p><Button size="sm" onclick={()=>void scan()}>{t('uScanAgain')}</Button>
        {:else if !preview}<p class="muted">{t('uScanIdle')}</p>
        {:else}
          {#if preview.count}<p><strong>{fill('uScanResult',{n:preview.count.toLocaleString(app.locale),duration:duration(preview.seconds)})}</strong></p>
            {#if estimate!=null}<p class="muted">{fill('analysisEstimate',{time:roughTime(estimate)})}</p>{/if}
            {#if preview.first&&preview.last}<p>{fill('uScanRange',{first:localTime(preview.first),last:localTime(preview.last),zone:scanZone})}</p>{/if}
          {:else}<p><strong>{t(allRegistered?'uAllRegistered':'uNoSupportedAudio')}</strong></p><p>{t('uTryAnotherFolder')}</p>{/if}
          {#if preview.skipped.length&&!allRegistered}<details open={!preview.count}><summary>{fill('uSkippedFiles',{n:preview.skipped.length})}</summary>
            <ul>{#each reasons as reason}<li>{fill('uSkipReason',{reason:t(`skip_${reason[0]}`),n:reason[1]})}</li>{/each}</ul>
            <ul class="files">{#each preview.skipped.slice(0,20) as item}<li>{fileName(item.path)} — {t(`skip_${item.code}`)}</li>{/each}
              {#if preview.skipped.length>20}<li>{fill('uSkippedMore',{n:preview.skipped.length-20})}</li>{/if}</ul></details>{/if}
        {/if}
        {#if register.error}<p class="error" role="alert">{t(register.error)}</p>{/if}
      </div>
      <Button variant="primary" pending={register.pending} disabled={!missing&&!!blocked} aria-describedby="add-reason" onclick={()=>{if(missing){tried=true;void focusMissing();}else register.run();}}>{preview?.count?fill('uAddTheseFiles',{n:preview.count.toLocaleString(app.locale)}):t('uAddRecordings')}</Button>
      <p class="reason {tried&&missing?'field-error':'muted'}" id="add-reason">{register.pending?'':missing||(blocked&&t(blocked))}</p>
    </section>
  </div>
</div>
<style>.busy{display:flex;align-items:center;gap:8px}ul{margin:8px 0;padding-left:24px}.files{color:rgb(var(--stone-600))}.reason{min-height:24px;margin-top:-4px}</style>
