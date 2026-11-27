<script lang="ts" module>
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { fill } from './workflow';
  // `id` is null when the species list has no row for the scientific name.
  // `own`: heard in a picked sound as the sound map itself recorded it, not another screen.
  export type Named={id:string|null;scientific_name:string;own?:boolean};
  // `species` and `background`: the species heard in the sound on any screen, first heard first. `complete`: the map's own
  // "nothing else calls in it"; `no_bird`: another screen recorded "no bird sound".
  // `others`: what older versions asked ("other species heard"), kept as stored.
  export type Label={species:Named|null;background:Named[];complete:boolean;no_bird?:boolean;sound_type:''|'song'|'call';quality:''|'A'|'B'|'C'|'D'|'E';others:boolean};
  export type Answer='present'|'withdraw'|'complete'|'incomplete';
  // `clip_start_s`–`clip_end_s`: the part of the recording that is exported (the interval and a short margin), as the sidecar reports it;
  // a sound ticked a moment ago has none until the list is read again.
  export type Item=Label&{recording_id:number;start_s:number;end_s:number;clip_start_s?:number;clip_end_s?:number;path:string;recorded_at:string;timezone:string;exported_at:string|null;updated_at:string};
  type Interval={recording_id:number;start_s:number;end_s:number};
  // Picked sounds are keyed by recording and interval, as the sidecar stores them.
  export const pickKey=(s:Interval)=>`${s.recording_id}:${s.start_s}:${s.end_s}`;
  /** What is still blank, such as "2 without species, 5 without quality"; empty when nothing is. */
  export const blanks=(items:Item[])=>([['eNoSpecies',items.filter(i=>!i.species&&!i.complete&&!i.no_bird).length],['eNoQuality',items.filter(i=>!i.quality).length]] as const)
    .filter(([,n])=>n).map(([key,n])=>fill(key,{n})).join(t('eSep'));
  /** The picked sounds of one site. Changes are shown at once and sent one after another, so the last one wins; the list is read again after the last. */
  export function pickedSounds(dataset:number){
    const shared=$state({items:{} as Record<string,Item>,recent:[] as Named[],loaded:false,error:''});
    let queue:Promise<unknown>=Promise.resolve(),version=0;
    const fail=(e:unknown)=>{shared.error=e instanceof Error?e.message:'unknown_error';};
    async function reload(){const at=version;
      try{const {items,recent}=await api<{items:Item[];recent:Named[]}>(`/explore/picked?dataset=${dataset}`);
        if(at===version){shared.recent=recent??[];shared.items=Object.fromEntries(items.map(item=>[pickKey(item),item]));shared.loaded=true;}}catch(e){fail(e);}}
    function send(change:()=>Promise<unknown>){const at=++version;shared.error='';
      return queue=queue.then(change).catch(fail).then(()=>{if(at===version)return reload();});}
    const where=(s:Interval)=>`dataset=${dataset}&recording_id=${s.recording_id}&start_s=${s.start_s}&end_s=${s.end_s}`;
    // `idle` settles once every change sent so far is saved.
    return {state:shared,reload,send,idle:()=>queue,
      save(item:Item){shared.items[pickKey(item)]=item;
        return send(()=>api('/explore/picked',{dataset_id:dataset,recording_id:item.recording_id,start_s:item.start_s,end_s:item.end_s,
          sound_type:item.sound_type,quality:item.quality,others:item.others},'PUT'));},
      // What the map records on a picked sound: a species heard or its own record of one withdrawn, or nothing else calls in it (or that taken back).
      answer(item:Item,verdict:Answer,species?:Named){
        const all=[...item.species?[item.species]:[],...item.background],rest=all.filter(s=>s.scientific_name!==species?.scientific_name);
        const heard=!species?all:verdict==='present'?[...rest,{...species,own:true}]:rest;
        // Shown at once, roughly as the sidecar will store it (a species heard takes "no voice" back); the list read after it is exact.
        shared.items[pickKey(item)]={...item,species:heard[0]??null,background:heard.slice(1),complete:!species?verdict==='complete':verdict==='present'?item.complete&&all.length>0:item.complete};
        return send(()=>api('/heard/map-answer',{dataset_id:dataset,recording_id:item.recording_id,start_s:item.start_s,end_s:item.end_s,verdict,...species?{species_id:species.id}:{}}));},
      remove(s:Interval){delete shared.items[pickKey(s)];return send(()=>api(`/explore/picked?${where(s)}`,undefined,'DELETE'));}};
  }
</script>
<script lang="ts">
  import { untrack } from 'svelte';
  import SpeciesChoice from './SpeciesChoice.svelte';
  // What the sound map records on one picked sound, entered in place and saved at once: the species heard (press to add or remove),
  // whether anything else calls in it, and the call type and quality for the ZIP's table. What other screens recorded
  // on the sound is shown in one line and cannot be changed here.
  // `first`: species offered before the recent ones (the one searched for, the AI's answers for the sound); `recent`: those
  // entered last, as they were when the form opened, so no chip moves while it is used. Six are offered at most.
  let { value, datasetId, first=[], recent=[], name, onchange, onanswer }:{value:Label;datasetId:number;first?:Named[];recent?:Named[];
    name:(s:Named)=>string;onchange:(label:Label)=>void;onanswer:(verdict:Answer,species?:Named)=>void}=$props();
  let guide=$state(false),finding=$state(false);
  const before=untrack(()=>recent);
  const heard=$derived([...value.species?[value.species]:[],...value.background]);
  const mine=$derived(heard.filter(h=>h.own)),elsewhere=$derived(heard.filter(h=>!h.own));
  const on=(s:Named)=>mine.some(h=>h.scientific_name===s.scientific_name);
  const offered=$derived([...new Map([...first,...before].filter(s=>s.id).map(s=>[s.scientific_name,s])).values()].slice(0,6));
  const chips=$derived([...offered,...mine.filter(h=>!offered.some(s=>s.scientific_name===h.scientific_name))]);
  function change(patch:Partial<Label>){
    const next={...value,...patch};
    if(next.sound_type!==value.sound_type||next.quality!==value.quality)onchange(next);
  }
  const TYPES=[['song','pfSong'],['call','pfCall'],['','pfUnknown']] as const;
  const QUALITY=['A','B','C','D','E'] as const;
</script>
<div class="pick-form">
  <div class="chips" role="group" aria-label={t('pfHeard')}><span class="name">{t('pfHeard')}</span>
    {#each chips as s (s.scientific_name)}<button type="button" class="chip" aria-pressed={on(s)} onclick={()=>onanswer(on(s)?'withdraw':'present',s)}>{name(s)}</button>{/each}
    <button type="button" class="chip find" aria-expanded={finding} onclick={()=>finding=!finding}>{t('pfFind')}</button></div>
  {#if finding}<SpeciesChoice {datasetId} exclude={mine.flatMap(s=>s.id?[s.id]:[])} onchoose={taxon=>{finding=false;onanswer('present',{id:taxon.id,scientific_name:taxon.scientific_name});}}/>{/if}
  {#if elsewhere.length||value.no_bird}<p class="muted elsewhere">{fill('pfElsewhere',{list:[...elsewhere.map(name),...value.no_bird?[t('ruNotBird')]:[]].join(t('eSep'))})}</p>{/if}
  <label class="none"><input type="checkbox" checked={value.complete} onchange={event=>onanswer(event.currentTarget.checked?'complete':'incomplete')}/>{t(heard.length?'pfNoMore':'pfNone')}</label>
  <div class="grid">
    <span class="name">{t('pfVoice')}</span>
    <span class="segmented" role="group" aria-label={t('pfVoice')} title={t('pfVoiceHint')}>{#each TYPES as [type,key] (type)}
      <button type="button" aria-pressed={value.sound_type===type} onclick={()=>change({sound_type:type})}>{t(key)}</button>{/each}</span>
    <span class="name">{t('pfQuality')}</span>
    <!-- Pressing the chosen grade again clears it: a grade nobody chose is left blank rather than guessed. -->
    <span class="controls"><span class="segmented" role="group" aria-label={t('pfQuality')}>{#each QUALITY as grade (grade)}
      <button type="button" aria-pressed={value.quality===grade} onclick={()=>change({quality:value.quality===grade?'':grade})}>{grade}</button>{/each}</span>
      <button type="button" class="link" aria-expanded={guide} onclick={()=>guide=!guide}>{t('pfGuide')}</button></span>
  </div>
  {#if guide}<div class="guide"><dl>{#each QUALITY as grade (grade)}<dt>{grade}</dt><dd>{t(`pfQ${grade}`)}</dd>{/each}</dl><p class="muted">{t('pfGuideNote')}</p></div>{/if}
  {#if mine.length||value.complete}{@const list=mine.map(name).join(t('eSep'))}<p class="saved" role="status">{fill('pfSaved',{what:!mine.length?t(heard.length?'pfNoMore':'pfNone'):value.complete?fill('pfAndNoMore',{list}):list})}</p>{/if}
</div>
<style>
  .pick-form{display:flex;flex-direction:column;gap:6px}
  .chips,.controls{display:flex;flex-wrap:wrap;align-items:center;gap:6px}
  .name{margin-right:2px;font-size:14px;font-weight:500;color:rgb(var(--stone-800))}
  /* A species is a round button: filled when it was heard in the sound. */
  .pick-form .chip{min-height:36px;padding:2px 14px;border:1px solid rgb(var(--stone-600));border-radius:999px;background:rgb(var(--color-card-bg));color:inherit;font:inherit;font-size:15px;cursor:pointer}
  .pick-form .chip[aria-pressed="true"]{border-color:rgb(var(--primary-700));background:rgb(var(--primary-700));color:#fffaf3}
  .pick-form .chip.find{border-style:dashed}
  .none{display:flex;align-items:center;gap:8px;min-height:44px;font-weight:400;cursor:pointer}.none input{width:24px;height:24px}
  .grid{display:grid;grid-template-columns:max-content minmax(0,1fr);align-items:center;justify-items:start;gap:4px 10px}
  .link{min-height:44px;padding:0 4px;font-size:14px}
  .elsewhere{margin:0;font-size:14px}
  .saved{margin:0;color:rgb(var(--color-success));font-size:14px}
  .guide{padding:8px 12px;border-radius:8px;background:rgb(var(--stone-100));font-size:14px}
  .guide dl{display:grid;grid-template-columns:2em 1fr;gap:2px 8px;margin:0}.guide dt{font-weight:600}.guide dd{margin:0}
</style>
