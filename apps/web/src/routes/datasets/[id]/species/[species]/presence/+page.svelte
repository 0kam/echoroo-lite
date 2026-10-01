<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { untrack } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { fill,modelName,presenceText,rangeQuery,type PresenceList,type PresenceRow } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import ReferencePanel from '$lib/ReferencePanel.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const query=$derived(new URL(page.url.hash.slice(1),page.url).searchParams);
  const source=$derived(query.get('source')==='search'?'search':'ai');
  const dates=$derived(new URLSearchParams([...query].filter(([key])=>key.endsWith('_date'))).toString());
  // Shown rows and played sounds survive going back to this page.
  const draftKey=$derived(`echoroo-presence:${id}:${speciesId}:${source}:${dates}`);
  let list=$state<PresenceList|null>(null),error=$state(''),shown=$state(10),played=$state<string[]>([]),saved=$state(0),ready=$state('');
  // BirdNET (3 s) and Perch (5 s) rows can start at the same second.
  const key=(row:PresenceRow)=>`${row.recording_id}:${row.start_s}:${row.end_s}`;
  async function load(){try{list=await api<PresenceList>(`/datasets/${id}/species/${speciesId}/presence?source=${source}${dates?`&${dates}`:''}`);}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}}
  $effect(()=>{const current=draftKey;untrack(()=>{list=null;error='';saved=0;shown=10;played=[];
    try{const draft=JSON.parse(sessionStorage.getItem(current)||'null');if(draft){shown=draft.shown;played=draft.played;}}catch{/* A lost draft only resets the list position. */}
    ready=current;void load();});});
  $effect(()=>{const value=JSON.stringify({shown,played});if(ready===draftKey)try{sessionStorage.setItem(draftKey,value);}catch{/* Private windows may refuse storage. */}});
  const range=$derived(list?rangeQuery(list.start_date,list.end_date):dates);
  const back=$derived(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}`);
  const other=(name:'ai'|'search')=>`${resolve(`/datasets/${id}/species/${speciesId}/presence`)}?${range}&source=${name}`;
  // "Listened to the top N": the longest run of played sounds from the top of the list.
  const listened=$derived(list?(()=>{const index=list.rows.findIndex(row=>!played.includes(key(row)));return index<0?list.rows.length:index;})():0);
  const anyMarked=$derived(!!list?.rows.some(row=>row.marked));
  const mark=createTask(async(row:PresenceRow)=>{
    await api('/presence/marks',{dataset_id:id,species_id:speciesId,recording_id:row.recording_id,start_s:row.start_s,end_s:row.end_s,run_id:row.run_id,marked:!row.marked});
    row.marked=!row.marked;saved=0;await load();
  },{toast:true});
  const recordMiss=createTask(async()=>{if(!list)return;
    await api('/presence/misses',{dataset_id:id,species_id:speciesId,start_date:list.start_date,end_date:list.end_date,source,listened});
    saved=listened;await load();
  },{toast:true});
  const flagged=(row:PresenceRow)=>row.models.map(model=>fill('pRank',{model:modelName(model.engine),rank:model.rank})).join('・');
  const time=(row:PresenceRow)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:list?.site.timezone}).format(new Date(row.local_time));
  const practice=$derived(`${resolve('/quiz')}?species=${speciesId}&return=${encodeURIComponent(`/datasets/${id}/species/${speciesId}/presence?${range}&source=${source}`)}`);
</script>
<div class="page wide presence-page">
  <div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if list}
    <h1>{fill('pTitle',{name:commonName(list.species,app.locale)||list.species.scientific_name})}</h1>
    <p>{t('pHint')}</p>
    {#if list.has_ai&&list.run_id}<div class="actions" role="group" aria-label={t('sPresenceTitle')}>
      <Button active={source==='ai'} aria-pressed={source==='ai'} onclick={()=>goto(other('ai'))}>{t('pSourceAi')}</Button>
      <Button active={source==='search'} aria-pressed={source==='search'} onclick={()=>goto(other('search'))}>{t('pSourceSearch')}</Button></div>{/if}
    <div class="with-examples"><section class="panel">
      <p class="status" role="status"><strong>{presenceText(list.presence)}</strong></p>
      {#each list.rows.slice(0,shown) as row,index (key(row))}<article class="row" class:marked={row.marked}>
        <div class="row-head"><span class="rank">{index+1}</span><span>{time(row)}</span>
          <span class="muted">{source==='search'?fill('pSimilarity',{value:(row.score??0).toFixed(2)}):flagged(row)}</span></div>
        <AudioRegion source={row.source} fixedRange compact start={row.start_s} end={row.end_s} onplaying={()=>{if(!played.includes(key(row)))played=[...played,key(row)];}}>
          {#snippet actions()}<Button variant={row.marked?'primary':'default'} aria-pressed={row.marked} pending={mark.pending} onclick={()=>mark.run(row)}>{row.marked?'✓ ':''}{t('pMark')}</Button>{/snippet}
        </AudioRegion>
      </article>{:else}<p>{t('pNoRows')}</p>{/each}
      {#if shown<list.rows.length}<Button onclick={()=>shown+=10}>{t('pMore')}</Button>{/if}
      {#if !anyMarked&&list.rows.length}<div class="miss">
        <p>{listened?fill('pListened',{n:listened}):t('pListenedNone')}</p>
        {#if saved}<p role="status">✓ {fill('pMissSaved',{n:saved})}</p>
        {:else}<Button disabled={!listened} pending={recordMiss.pending} onclick={()=>recordMiss.run()}>{t('pRecordMiss')}</Button>{/if}</div>{/if}
      <p class="muted">{t('pEvidenceNote')}</p>
      <div class="actions"><Button variant="primary" href={back}>{t('wBackSpecies')}</Button></div>
    </section>
    <div class="examples-column"><ReferencePanel species={list.species} datasetId={id} {practice}/></div></div>
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.with-examples{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,380px);gap:16px;align-items:start}
  .examples-column{position:sticky;top:16px}@media(max-width:950px){.with-examples{grid-template-columns:minmax(0,1fr)}.examples-column{position:static}}
  p{margin:8px 0}.status{font-size:18px}.row{display:flex;flex-direction:column;gap:8px;padding:16px 0;border-top:1px solid rgb(var(--stone-300))}
  .row.marked{border-left:4px solid rgb(var(--primary-700));padding-left:12px}
  .row-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}.rank{font-weight:700}.miss{margin:16px 0;padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px}</style>
