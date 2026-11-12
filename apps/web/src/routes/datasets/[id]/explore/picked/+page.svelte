<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,fileName,siteOf } from '$lib/app.svelte';
  import { chooseDirectory } from '$lib/files';
  import { commonName,type Species } from '$lib/species';
  import { fill } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import PickForm,{blanks,pickedSounds,pickKey,recentSpecies,type Item,type Named} from '$lib/PickForm.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  const id=Number(page.params.id),picks=pickedSounds(id);
  const site=$derived(siteOf(datasetOf(id)));
  void picks.reload();
  const items=$derived(Object.values(picks.state.items));
  const missing=$derived(blanks(items)),recent=$derived(recentSpecies(items));
  // Sounds with a species name, one entry per species, to hand to that species' search (which takes at most 20 example calls).
  const named=$derived(items.flatMap(i=>i.species?[i.species]:[]));
  const groups=$derived([...new Map(named.map(s=>[s.scientific_name,s])).values()]
    .map(s=>({s,n:Math.min(20,named.filter(o=>o.scientific_name===s.scientific_name).length)})));
  let folder=$state(''),saved=$state<{path:string;count:number}|null>(null);
  // A change that fails may be far down the list, so its error is shown as a toast; a list that cannot load says so in place.
  // `untrack`: the toast list itself must not re-run this, or one failure would raise toasts forever.
  $effect(()=>{const failed=picks.state.error;if(failed&&picks.state.loaded)untrack(()=>notify(failed,'error'));});
  // Common names in the UI language are fetched once per species; the scientific name is shown until then or when there is none.
  let names=$state<Record<string,Species>>({});const asked=new Set<string>();
  $effect(()=>{for(const {id:sid} of items.flatMap(i=>[...i.species?[i.species]:[],...i.background]))if(sid&&!asked.has(sid)){asked.add(sid);
    api<Species>(`/species/${encodeURIComponent(sid)}`).then(row=>{names[sid]=row;},()=>{/* The scientific name stays as the fallback. */});}});
  const speciesName=(s:Named)=>{const row=s.id?names[s.id]:undefined;return (row&&commonName(row,app.locale))||s.scientific_name;};
  // Recording times are shown in the site's timezone, whatever the PC's.
  const when=(i:Item)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:i.timezone}).format(new Date(Date.parse(i.recorded_at)+i.start_s*1000));
  const where=(i:Item)=>`${when(i)} · ${fill('pkWhere',{file:fileName(i.path),start:+i.start_s.toFixed(1),end:+i.end_s.toFixed(1)})}`;
  // One sound at a time: starting a player pauses every other one on the page.
  function solo(event:Event){for(const audio of (event.currentTarget as HTMLElement).querySelectorAll('audio'))if(audio!==event.target)audio.pause();}
  // A label typed just before pressing is saved first, so the search page receives it.
  async function searchWith(s:Named){await picks.idle();await goto(`${resolve(`/datasets/${id}/search`)}?picked=${encodeURIComponent(s.scientific_name)}`);}
  const exportZip=createTask(async()=>{
    if(isTauri()){const path=await chooseDirectory();if(!path)return;folder=path;}
    saved=null;
    // The last label typed before pressing the button goes into the file.
    await picks.idle();
    saved=await api<{path:string;count:number}>('/explore/picked/export',{dataset_id:id,folder});
    // Exported sounds now carry their mark.
    await picks.reload();
  });
</script>
<div class="page narrow" onplaycapture={solo}>
  <div class="crumbs"><a href={resolve(`/datasets/${id}/explore`)}>{t('pkBack')}</a></div>
  <div class="page-header"><div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{t('pkTitle')}</h1><p>{t('pkLead')}</p></div></div>
  {#if picks.state.error&&!picks.state.loaded}<p class="error" role="alert">{t(picks.state.error)}</p>{/if}
  {#if picks.state.loaded}
    {#if groups.length}<section class="panel stack">
      <h2>{t('pkSearchHeading')}</h2>
      {#each groups as {s,n} (s.scientific_name)}<div class="species-row"><strong>{speciesName(s)}</strong><Button size="sm" onclick={()=>searchWith(s)}>{fill('pkSearchButton',{n})}</Button></div>{/each}
    </section>{/if}
    <section class="panel stack">
      <h2>{fill('pkListHeading',{n:items.length})}</h2>
      {#if items.length}<p>{t('pkMargin')}</p>{/if}
      <!-- The figure and the play button cover what is exported: the range the sidecar gives with each sound. The lines mark the interval heard on the map.
           A sound ticked a moment ago has no range until the list is read again; it shows the interval heard meanwhile. -->
      {#each items as item (pickKey(item))}{@const from=item.clip_start_s??item.start_s}{@const to=item.clip_end_s??item.end_s}<article class="item">
        <AudioRegion source={{id:item.recording_id,kind:'recording',path:item.path,seconds:to}} fixedRange compact dense start={from} end={to} marked={[item.start_s,item.end_s]}>
          {#snippet meta()}<span class="where">{where(item)}</span>{/snippet}
          {#snippet actions()}{#if item.exported_at}<span class="saved">{t('pkExported')}</span>{/if}<Button size="sm" onclick={()=>picks.remove(item)}>{t('pkRemove')}</Button>{/snippet}
        </AudioRegion>
        <div class="labels"><PickForm value={item} {recent} name={speciesName} onchange={label=>picks.save({...item,...label})}/></div>
      </article>
      {:else}<p class="muted">{t('pkEmpty')}</p>{/each}
    </section>
    <section class="panel stack">
      <h2>{t('pkExportHeading')}</h2>
      {#if missing}<p>{missing}<br>
        <span class="muted">{t('pkBlanksOk')}</span></p>{/if}
      {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder} disabled={exportZip.pending}/></label>{/if}
      <!-- Same shape as the report export: a status box that is always there, then one fixed main button. -->
      <div class="status-box" role="status" aria-live="polite">
        {#if exportZip.pending}<p><Spinner/> {t('pkExporting')}</p>
        {:else if exportZip.error}<p class="error">{t(exportZip.error)}</p>
        {:else if saved}<p><strong>{t('pkSaved')}</strong></p><p class="path">{saved.path}</p>
        {:else}<p>{fill('pkExportIntro',{n:items.length})}{#if isTauri()}{app.locale==='ja'?'':' '}{t('pkChooseLater')}{/if}</p>{/if}
      </div>
      <Button variant={saved?'default':'primary'} pending={exportZip.pending} disabled={!items.length||(!isTauri()&&!folder)} onclick={()=>exportZip.run()}>{t('pkExportButton')}</Button>
      {#if items.length&&!isTauri()&&!folder}<p class="muted">{t('rNeedFolder')}</p>{/if}
      <p class="muted">{t('pkNotIncluded')}</p>
    </section>
  {:else if !picks.state.error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>
  .item{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  /* The labels line up with the spectrogram, right of the play button. */
  .labels{padding-left:52px}
  /* The date, file and seconds wrap instead of being cut (English runs past one line). */
  .where{white-space:normal;overflow-wrap:anywhere}
  .saved{white-space:nowrap}
  .species-row{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px}
  .path{overflow-wrap:anywhere;color:rgb(var(--stone-600));font-size:13px}
  @media(max-width:600px){.labels{padding-left:0}}
</style>
