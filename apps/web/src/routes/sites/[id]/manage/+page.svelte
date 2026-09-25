<script lang="ts">
  import { onMount } from 'svelte';
  import { beforeNavigate, goto } from '$app/navigation';
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t,duration,refreshCatalog,type Site,type Dataset } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import SiteMap from '$lib/SiteMap.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Collection = Dataset & {count:number;seconds:number;first:string|null;last:string|null};
  type Details = {site:Site;datasets:Collection[]};
  type Removal = {site_id:number;dataset_id:number|null;name:string;revision:string;recordings:number;analyses:number;searches:number;validations:number;annotations:number;custom_models?:number;reports:number;exports:number};
  const id = Number(page.params.id);
  let details = $state<Details|null>(null), name=$state(''), latitude=$state<number|null>(null), longitude=$state<number|null>(null);
  let changingPlace=$state(false), removal=$state<Removal|null>(null), renameId=$state<number|null>(null), collectionName=$state('');
  let dialog: HTMLDialogElement, discardDialog: HTMLDialogElement;
  let destination: URL|null=null, leaving=false;
  const dirty=$derived(!!details&&(name!==details.site.name||latitude!==details.site.latitude||longitude!==details.site.longitude||renameId!==null));
  beforeNavigate(event=>{
    if(!dirty||leaving)return;
    event.cancel();
    if(!event.willUnload&&event.to){destination=event.to.url;discardDialog.showModal();}
  });
  const resultId=$derived(details?.datasets[0]?.id);
  function receive(value:Details){details=value;name=value.site.name;latitude=value.site.latitude;longitude=value.site.longitude;changingPlace=false;renameId=null;}
  const load=createTask(async()=>receive(await api(`/sites/${id}/management`)));
  onMount(()=>{void load.run();});
  const save=createTask(async()=>{receive(await api(`/sites/${id}/edit`,{name,latitude,longitude}));await refreshCatalog();},{toast:true});
  const rename=createTask(async()=>{receive(await api(`/sites/${id}/datasets/${renameId}/edit`,{name:collectionName}));await refreshCatalog();},{toast:true});
  const inspect=createTask(async(datasetId:number|null)=>{remove.error='';removal=await api(`/sites/${id}/delete-preview${datasetId===null?'':`?dataset_id=${datasetId}`}`);dialog.showModal();});
  const remove=createTask(async()=>{
    if(!removal)return;
    await api(`/sites/${id}/delete`,{dataset_id:removal.dataset_id,revision:removal.revision});
    app.recordingsVersion++;await refreshCatalog();dialog.close();
    if(removal.dataset_id===null){details=null;renameId=null;await goto(resolve('/datasets'));}
    else await load.run();
    removal=null;
  });
  const date=(value:string|null)=>value&&details?new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeZone:details.site.timezone}).format(new Date(value)):'—';
</script>
<div class="page management">
  <div class="crumbs"><a href={resolve('/datasets')}>{t('uSites')}</a>{#if resultId}<a href={resolve(`/datasets/${resultId}`)}>{t('uBackResults')}</a>{/if}</div>
  <h1>{t('mManage')}</h1>
  {#if details}
    <form class="panel stack" onsubmit={event=>{event.preventDefault();save.run();}}>
      <label>{t('siteName')}<input bind:value={name} disabled={renameId!==null||save.pending} required maxlength="200"/></label>
      <h2>{t('mLocation')}</h2>
      <SiteMap bind:latitude bind:longitude editable={changingPlace&&renameId===null&&!save.pending}/>
      {#if !changingPlace}<Button disabled={renameId!==null||save.pending} onclick={()=>changingPlace=true}>{t('mChangeLocation')}</Button>{/if}
      {#if changingPlace}<div class="form-grid"><label>{t('latitude')}<input type="number" step="any" min="-90" max="90" bind:value={latitude} disabled={save.pending} required/></label>
        <label>{t('longitude')}<input type="number" step="any" min="-180" max="180" bind:value={longitude} disabled={save.pending} required/></label></div>
        <p class="notice">{t('mLocationEffect')}</p>
      {:else}<p class="muted">{t('latitude')} {latitude?.toFixed(6)} · {t('longitude')} {longitude?.toFixed(6)}</p>{/if}
      <p class="muted">{t('uTimeZone')}: {details.site.timezone}</p>
      <div class="actions"><Button type="submit" variant="primary" pending={save.pending} disabled={!dirty||renameId!==null}>{t('mSave')}</Button>
        {#if (dirty||changingPlace)&&renameId===null}<Button disabled={save.pending} onclick={()=>receive(details!)}>{t('mCancel')}</Button>{/if}</div>
      {#if save.error}<p class="error" role="alert">{t(save.error)}</p>{/if}
    </form>
    <section class="panel"><div class="section-heading"><h2>{t('mImports')}</h2><Button href={`${resolve('/datasets/new')}?site=${id}&return=${encodeURIComponent(`/sites/${id}/manage`)}`}>{t('uAddRecordings')}</Button></div>
      {#each details.datasets as dataset}
        <div class="collection">
          {#if renameId===dataset.id}<form class="actions" onsubmit={event=>{event.preventDefault();rename.run();}}>
            <label>{t('mImportName')}<input bind:value={collectionName} disabled={rename.pending} required maxlength="200"/></label>
            <Button type="submit" pending={rename.pending}>{t('mSave')}</Button><Button disabled={rename.pending} onclick={()=>renameId=null}>{t('mCancel')}</Button>
          </form>{:else}<h3>{dataset.name}</h3>{/if}
          <p>{date(dataset.first)} — {date(dataset.last)} · {dataset.count} {t('files')} · {duration(dataset.seconds)}</p>
          <p class="muted folder">{dataset.folder}</p>
          {#if renameId===null}<div class="actions"><Button disabled={dirty||changingPlace} onclick={()=>{renameId=dataset.id;collectionName=dataset.name;}}>{t('mRename')}</Button>
            <Button disabled={dirty} pending={inspect.pending} onclick={()=>inspect.run(dataset.id)}>{t('mRemoveImport')}</Button></div>{/if}
        </div>
      {:else}<p>{t('mNoImports')}</p>{/each}
      {#if rename.error}<p class="error" role="alert">{t(rename.error)}</p>{/if}
    </section>
    <section class="panel"><h2>{t('mRemoveSite')}</h2><p>{t('mKeepFiles')}</p>
      <Button disabled={dirty} pending={inspect.pending} onclick={()=>inspect.run(null)}>{t('mRemoveSite')}</Button></section>
  {/if}
  {#if load.pending}<p role="status">{t('loading')}</p>{/if}
  {#if load.error||inspect.error}<p class="error" role="alert">{t(load.error||inspect.error)}</p>{/if}
</div>
<dialog bind:this={dialog} oncancel={event=>{if(remove.pending)event.preventDefault();}} aria-labelledby="remove-title">
  {#if removal}<h2 id="remove-title">{removal.name} — {t('mDeleteTitle')}</h2><p>{t('mDeleteEffect')}</p>
    <dl>{#each [['mRecordings',removal.recordings],['mAnalyses',removal.analyses],['mSearches',removal.searches],['mValidations',removal.validations],['aHeard',removal.annotations],['mCustomModels',removal.custom_models??0],['mReports',removal.reports],['mExports',removal.exports]] as [key,count]}{#if count||key==='mRecordings'}<div><dt>{t(String(key))}</dt><dd>{count}</dd></div>{/if}{/each}</dl>
    <p>{t('mKeepFiles')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>dialog.close()}>{t('mCancel')}</Button><Button variant="primary" pending={remove.pending} onclick={()=>remove.run()}>{t('mDeleteConfirm')}</Button></div>
    {#if remove.error}<p class="error" role="alert">{t(remove.error)}</p>{/if}
  {/if}
</dialog>
<dialog bind:this={discardDialog} aria-labelledby="discard-title">
  <h2 id="discard-title">{t('mDiscard')}</h2><div class="actions">
    <Button onclick={()=>discardDialog.close()}>{t('mCancel')}</Button>
    <Button onclick={()=>{if(destination){leaving=true;discardDialog.close();void goto(destination);}}}>{t('mLeave')}</Button>
  </div>
</dialog>
<style>.management{max-width:860px!important}.stack{gap:18px}.collection{padding:22px 0;border-bottom:1px solid rgb(var(--stone-200))}h3{margin:0}p{margin:12px 0}.folder{overflow-wrap:anywhere;font-size:13px}dialog{max-width:560px;width:calc(100% - 40px);padding:28px;border:1px solid #ccc;border-radius:12px;background:rgb(var(--stone-50));color:inherit}dialog::backdrop{background:#0006}dl>div{display:flex;justify-content:space-between;padding:5px 0}</style>
