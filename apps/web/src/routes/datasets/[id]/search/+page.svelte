<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { open } from '@tauri-apps/plugin-dialog';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,loadRecordings,fileName } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { rangeQuery,type SearchRun } from '$lib/workflow';
  import AudioRegion,{type AudioSource} from '$lib/AudioRegion.svelte';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import XenoCantoSearch from '$lib/XenoCantoSearch.svelte';
  import Button from '$lib/ui/Button.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Reference=AudioSource&{attribution?:{id:number;recordist:string;license:string}};
  type Target={species:Species;clips:{source:Reference;start_s:number;end_s:number}[]};
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  let species=$state<Species|null>(null),source=$state<Reference|null>(null),references=$state<Reference[]>([]),targets=$state<Target[]>([]);
  let first=$state(query.get('start_date')||''),last=$state(query.get('end_date')||''),start=$state(0),end=$state(5),referencePath=$state(''),error=$state('');
  let xc=$state(true),history=$state<SearchRun[]>([]);
  const valid=$derived(!!source&&Number.isFinite(start)&&Number.isFinite(end)&&end-start>=.2-1e-9&&end-start<=30&&start>=0&&end<=source.seconds);
  function chooseReference(row:Reference){xc=false;source=row;start=0;end=Math.min(5,row.seconds);}
  function saveDraft(){localStorage.setItem(`echoroo-search-${id}`,JSON.stringify(targets));}
  function addClip(){if(!species||!source||!valid)return;let row=targets.find(r=>r.species.id===species!.id);
    if(!row){if(targets.length>=20)throw Error('invalid_input');row={species,clips:[]};targets.push(row);}
    if(!row.clips.some(c=>c.source.id===source!.id&&c.source.kind===source!.kind&&c.start_s===start&&c.end_s===end)){if(row.clips.length>=20)throw Error('invalid_input');row.clips.push({source,start_s:start,end_s:end});}
    saveDraft();
  }
  const addReference=createTask(async()=>{if(isTauri()){const path=await open({multiple:false,filters:[{name:t('referenceAudio'),extensions:['wav','flac','mp3']}]});if(!path)return;referencePath=path;}
    chooseReference(await api<Reference>('/references',{path:referencePath}));xc=false;
  },{toast:true});
  const search=createTask(async()=>{addClip();if(!targets.length)return;
    const runs=await api<SearchRun[]>('/search-runs',{dataset_id:id,start_date:first||null,end_date:last||null,
      targets:targets.map(row=>({species_id:row.species.id,clips:row.clips.map(c=>({source_kind:c.source.kind,source_id:c.source.id,start_s:c.start_s,end_s:c.end_s}))}))});
    localStorage.removeItem(`echoroo-search-${id}`);
    await goto(resolve(`/datasets/${id}/search/${runs[0].id}`));
  },{toast:true});
  onMount(()=>{void(async()=>{try{
    try{targets=JSON.parse(localStorage.getItem(`echoroo-search-${id}`)||'[]');}catch{targets=[];}
    const [refs,runs]=await Promise.all([api<Reference[]>('/references'),api<SearchRun[]>(`/search-runs?dataset_id=${id}`)]);references=refs;history=runs;
    if(query.get('species'))species=await api<Species>(`/species/${encodeURIComponent(query.get('species')!)}`);
    if(query.get('recording')){const record=(await loadRecordings(id)).find(r=>String(r.id)===query.get('recording'));
      if(record){chooseReference({...record,kind:'recording'});xc=false;}}
  }catch(e){error=e instanceof Error?e.message:'unknown_error';}})();});
</script>
<div class="page search-page"><div class="crumbs"><a href={`${resolve(`/datasets/${id}`)}${first&&last?`?${rangeQuery(first,last)}`:''}`}>{t('wResults')}</a></div>
  <h1>{t(species?'wFurther':'wFind')}</h1><p class="muted">{t('wFindHint')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <section class="panel"><SpeciesPicker bind:value={species} disabled={search.pending}/></section>
  {#if species}
    <section class="panel"><h2>{t('wSource')}</h2><div class="actions"><Button active={xc} onclick={()=>xc=true}>{t('xcFind')}</Button>
      <Button pending={addReference.pending} disabled={!isTauri()&&!referencePath.trim()} onclick={()=>addReference.run()}>{t('referenceFromFile')}</Button></div>
      {#if !isTauri()}<label>{t('referencePath')}<input bind:value={referencePath}/></label>{/if}
      {#if xc}<XenoCantoSearch {species} onselect={chooseReference}/>{/if}
      {#if references.length}<details><summary>{t('referenceAudio')}</summary>{#each references as ref}<p><Button onclick={()=>{chooseReference(ref);xc=false;}}>{fileName(ref.path)}</Button></p>{/each}</details>{/if}
      {#if source}{#key `${source.kind}:${source.id}`}<div class="reference-player"><p>{fileName(source.path)}</p>
        <AudioRegion {source} bind:start bind:end/>
        {#if source.attribution}<p class="muted">XC{source.attribution.id} · {source.attribution.recordist} · {source.attribution.license}</p>{/if}</div>{/key}
        <Button disabled={!valid||search.pending||(targets.find(r=>r.species.id===species?.id)?.clips.length??0)>=20} onclick={()=>{addClip();source=null;}}>{t('addQueryClip')}</Button>
      {/if}
    </section>
  {/if}
  {#if targets.length}<section class="panel"><h2>{t('wQueries')}</h2>{#each targets as target,index}<div class="section-heading"><span>{commonName(target.species,app.locale)} · <i>{target.species.scientific_name}</i> · {target.clips.length} {t('quizClips')}</span>
    <Button size="sm" disabled={search.pending} onclick={()=>{targets.splice(index,1);saveDraft();}}>{t('removeReference')}</Button></div>{/each}
    <Button disabled={search.pending||targets.length>=20} onclick={()=>{species=null;source=null;}}>{t('wAddTarget')}</Button></section>{/if}
  {#if species||targets.length}<section class="panel"><p class="muted">{first||t('noDateLimit')}–{last||t('noDateLimit')} · {t('wAutoSaved')}</p>
    <Button variant="primary" pending={search.pending} disabled={!!activeJob()||(!targets.length&&(!species||!valid))} onclick={()=>search.run()}>{t('wSearchAction')}</Button></section>{/if}
  {#if history.length}<details><summary>{t('savedSearches')}</summary>{#each history as run}<p><a href={resolve(`/datasets/${id}/search/${run.id}`)}>{commonName(run.species,app.locale)} · {run.created_at}</a></p>{/each}</details>{/if}
</div>
<style>.search-page{max-width:1050px}h1{margin:16px 0}h2{margin-bottom:16px}.reference-player{margin:20px 0}.actions{margin-bottom:16px}details{margin-top:16px}</style>
