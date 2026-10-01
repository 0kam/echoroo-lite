<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onDestroy, onMount, untrack } from 'svelte';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from '$lib/api.svelte';
  import { t,activeJob,dayRange,fileName } from '$lib/app.svelte';
  import { type Species } from '$lib/species';
  import { rangeQuery } from '$lib/workflow';
  import AudioRegion,{type AudioSource} from '$lib/AudioRegion.svelte';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import XenoCantoSearch from '$lib/XenoCantoSearch.svelte';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Reference=AudioSource&{attribution?:{id:number;recordist:string;license:string}};
  type Clip={source:Reference;start_s:number;end_s:number};
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  // Example calls belong to one species; choosing another species starts a new list.
  let species=$state<Species|null>(null),source=$state<Reference|null>(null),clips=$state<Clip[]>([]),clipsFor=$state('');
  let first=$state(query.get('start_date')||''),last=$state(query.get('end_date')||''),start=$state(0),end=$state(5),referencePath=$state(''),error=$state('');
  let ready=$state(false);
  // Two ways here: a species the AI list lacks (from site results) or a species page.
  const fromSpecies=query.get('species'),speciesState=new URLSearchParams([...query].filter(([key])=>['start_date','end_date','engine','model_id'].includes(key)));
  const back=fromSpecies?{href:`${resolve(`/datasets/${id}/species/${fromSpecies}`)}?${speciesState}`,label:'wBackSpecies'}
    :{href:`${resolve(`/datasets/${id}`)}${first&&last?`?${rangeQuery(first,last)}`:''}`,label:'uBackResults'};
  let alive=true;
  let xc=$state(true);
  onDestroy(()=>{alive=false;});
  $effect(()=>{const chosen=species?.id??'';untrack(()=>{if(ready&&chosen!==clipsFor){clips=[];clipsFor=chosen;if(!chosen)source=null;}});});
  $effect(()=>{if(ready)saveDraft();});
  const valid=$derived(!!source&&Number.isFinite(start)&&Number.isFinite(end)&&end-start>=.2-1e-9&&end-start<=30&&start>=0&&end<=source.seconds);
  function chooseReference(row:Reference){source=row;start=0;end=Math.min(5,row.seconds);}
  function saveDraft(){localStorage.setItem(`echoroo-search-${id}`,JSON.stringify({clips,clipsFor,species,source,start,end,xc}));}
  function addClip(){if(!source||!valid)return;
    if(!clips.some(c=>c.source.id===source!.id&&c.source.kind===source!.kind&&c.start_s===start&&c.end_s===end)){if(clips.length>=20)throw Error('invalid_input');clips.push({source,start_s:start,end_s:end});}
    saveDraft();
  }
  const addReference=createTask(async()=>{if(isTauri()){const path=await open({multiple:false,filters:[{name:t('referenceAudio'),extensions:['wav','flac','mp3']}]});if(!path)return;referencePath=path;}
    chooseReference(await api<Reference>('/references',{path:referencePath}));xc=false;
  },{toast:true});
  const search=createTask(async()=>{addClip();if(!species||!clips.length)return;
    await api('/search-runs',{dataset_id:id,species_id:species.id,start_date:first||null,end_date:last||null,
      clips:clips.map(c=>({source_kind:c.source.kind,source_id:c.source.id,start_s:c.start_s,end_s:c.end_s}))});
    if(!alive)return;
    const found=species;
    clips=[];clipsFor='';species=null;source=null;localStorage.removeItem(`echoroo-search-${id}`);ready=false;
    // Next: listen to the search results and confirm the species.
    await goto(`${resolve(`/datasets/${id}/species/${found.id}/presence`)}?${first&&last?`${rangeQuery(first,last)}&`:''}source=search`);
  });
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  onMount(()=>{void(async()=>{try{
    try{const saved=JSON.parse(localStorage.getItem(`echoroo-search-${id}`)||'null');
      if(saved){clips=saved.clips??[];clipsFor=saved.clipsFor??'';species=saved.species??null;source=saved.source??null;start=saved.start??0;end=saved.end??5;xc=saved.xc??true;}}
    catch{clips=[];}
    ready=true;
    if(query.get('species')){const chosen=await api<Species>(`/species/${encodeURIComponent(query.get('species')!)}`);if(chosen.id!==species?.id)species=chosen;}
  }catch(e){error=e instanceof Error?e.message:'unknown_error';}})();});
</script>
<div class="page search-page"><div class="crumbs"><a href={back.href}>{t(back.label)}</a></div>
  <h1>{t(fromSpecies?'sSearchTitle':'sFindMissing')}</h1><p class="lead">{t('wFindHint')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <!-- Same numbered steps as adding recordings: species, example calls, search. -->
  <div class="steps">
  <section class="panel step-card"><h2>{t('sSearchStepSpecies')}</h2><SpeciesPicker bind:value={species} disabled={search.pending}/></section>
  {#if species}
    <section class="panel step-card"><h2>{t('wSource')}</h2><div class="actions"><Button active={xc} disabled={search.pending} onclick={()=>{xc=true;source=null;}}>{t('xcFind')}</Button>
      <Button pending={addReference.pending} disabled={search.pending||(!isTauri()&&!referencePath.trim())} onclick={()=>addReference.run()}>{t('referenceFromFile')}</Button></div>
      {#if !isTauri()}<label>{t('referencePath')}<input bind:value={referencePath} disabled={search.pending}/></label>{/if}
      {#if xc&&!search.pending}{#key species.id}<div hidden={!!source}><XenoCantoSearch {species} onselect={chooseReference}/></div>{/key}{/if}
      {#if source}<Button disabled={search.pending} onclick={()=>source=null}>{t('uBackSounds')}</Button>{#key `${source.kind}:${source.id}`}<div class="reference-player" inert={search.pending} aria-disabled={search.pending}><p>{fileName(source.path)}</p>
        <AudioRegion {source} bind:start bind:end/>
        {#if source.attribution}<p class="muted">XC{source.attribution.id} · {source.attribution.recordist} · {source.attribution.license}</p>{/if}</div>{/key}
        <Button disabled={!valid||search.pending||clips.length>=20} onclick={()=>{addClip();source=null;}}>{t('addQueryClip')}</Button>
      {/if}
      {#if clips.length}<h3>{t('wQueriesAdded')}</h3><div class="added-clips">{#each clips as clip,index}<div class="actions"><Button disabled={search.pending} onclick={()=>{source=clip.source;start=clip.start_s;end=clip.end_s;}}>{fileName(clip.source.path)} · {clip.start_s.toFixed(1)}–{clip.end_s.toFixed(1)} {t('seconds')}</Button><Button disabled={search.pending} aria-label={`${t('removeReference')}: ${fileName(clip.source.path)}`} onclick={()=>{clips.splice(index,1);saveDraft();}}>{t('removeReference')}</Button></div>{/each}</div>{/if}
    </section>
    <section class="panel step-card"><h2>{t('sSearchStepRun')}</h2><p class="muted">{#if first&&last}{dayRange(first,last)} · {/if} {t('wAutoSaved')}</p>
      {#if search.pending}<p class="muted" role="status">{t('searchingAudioStatus')}</p>{/if}
      <Button variant="primary" pending={search.pending} disabled={!!activeJob()||(!clips.length&&!valid)} onclick={()=>search.run()}>{search.pending?t('searchingAudio'):t('wSearchAction')}</Button>
      {#if search.pending}<Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>{/if}
      {#if search.error}<p class="error" role="alert">{t(search.error)}{#if search.error==='search_exists'} · <a href={`${resolve(`/datasets/${id}/species/${species.id}`)}${first&&last?`?${rangeQuery(first,last)}`:''}`}>{t('wOpenSpecies')}</a>{/if}</p>{/if}</section>
  {/if}
  </div>
</div>
<style>h2{margin-bottom:16px}h3{margin:24px 0 8px}.reference-player{margin:24px 0}.actions{margin-bottom:16px}</style>
