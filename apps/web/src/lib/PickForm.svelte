<script lang="ts" module>
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { fill } from './workflow';
  // `id` is null when the species list has no row for the scientific name.
  export type Named={id:string|null;scientific_name:string};
  export type Label={species:Named|null;background:Named[];sound_type:''|'song'|'call';quality:''|'A'|'B'|'C'|'D'|'E';others:boolean};
  export type Item=Label&{recording_id:number;start_s:number;end_s:number;path:string;recorded_at:string;timezone:string;exported_at:string|null;updated_at:string};
  type Interval={recording_id:number;start_s:number;end_s:number};
  // Picked sounds are keyed by recording and interval, as the sidecar stores them.
  export const pickKey=(s:Interval)=>`${s.recording_id}:${s.start_s}:${s.end_s}`;
  /** What is still blank, such as "2 without species, 5 without quality"; empty when nothing is. */
  export const blanks=(items:Item[])=>([['eNoSpecies',items.filter(i=>!i.species).length],['eNoQuality',items.filter(i=>!i.quality).length]] as const)
    .filter(([,n])=>n).map(([key,n])=>fill(key,{n})).join(t('eSep'));
  /** Species of the given picked sounds, most recently updated first, distinct by scientific name, at most three. */
  export const recentSpecies=(items:Item[])=>[...new Map(items.filter(i=>i.species).sort((a,b)=>b.updated_at.localeCompare(a.updated_at))
    .map(i=>[i.species!.scientific_name,i.species!])).values()].slice(0,3);
  // A species search opens with its field focused, without moving the page.
  export const focusInput=(node:HTMLElement)=>{node.querySelector('input')?.focus({preventScroll:true});};
  /** The picked sounds of one site. Changes are shown at once and sent one after another, so the last one wins; the list is read again after the last. */
  export function pickedSounds(dataset:number){
    const shared=$state({items:{} as Record<string,Item>,loaded:false,error:''});
    let queue:Promise<unknown>=Promise.resolve(),version=0;
    const fail=(e:unknown)=>{shared.error=e instanceof Error?e.message:'unknown_error';};
    async function reload(){const at=version;
      try{const {items}=await api<{items:Item[]}>(`/explore/picked?dataset=${dataset}`);
        if(at===version){shared.items=Object.fromEntries(items.map(item=>[pickKey(item),item]));shared.loaded=true;}}catch(e){fail(e);}}
    function send(change:()=>Promise<unknown>){const at=++version;shared.error='';
      return queue=queue.then(change).catch(fail).then(()=>{if(at===version)return reload();});}
    const where=(s:Interval)=>`dataset=${dataset}&recording_id=${s.recording_id}&start_s=${s.start_s}&end_s=${s.end_s}`;
    // `idle` settles once every change sent so far is saved.
    return {state:shared,reload,send,idle:()=>queue,
      save(item:Item){shared.items[pickKey(item)]=item;
        return send(()=>api('/explore/picked',{dataset_id:dataset,recording_id:item.recording_id,start_s:item.start_s,end_s:item.end_s,
          species:item.species?.scientific_name??null,background:item.background.map(b=>b.scientific_name),sound_type:item.sound_type,quality:item.quality,others:item.others},'PUT'));},
      remove(s:Interval){delete shared.items[pickKey(s)];return send(()=>api(`/explore/picked?${where(s)}`,undefined,'DELETE'));}};
  }
</script>
<script lang="ts">
  import type { Species } from './species';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import Button from './ui/Button.svelte';
  // The labels of one picked sound; every change reports the whole label and the parent saves it.
  // `recent`: species recently entered on this site's other sounds, offered as one-tap choices while the species is blank; nothing is filled in on its own.
  let { value, recent=[], name, onchange }:{value:Label;recent?:Named[];name:(s:Named)=>string;onchange:(label:Label)=>void}=$props();
  let editing=$state<'species'|'other'|null>(null),guide=$state(false);
  const named=(row:Species):Named=>({id:row.id,scientific_name:row.scientific_name});
  function change(patch:Partial<Label>){
    const next={...value,...patch};
    if(JSON.stringify([next.species,next.background,next.sound_type,next.quality,next.others])!==JSON.stringify([value.species,value.background,value.sound_type,value.quality,value.others]))onchange(next);
  }
  const TYPES=[['song','pfSong'],['call','pfCall'],['','pfUnknown']] as const;
  const QUALITY=['A','B','C','D','E'] as const;
</script>
<!-- One row per item with the names in their own column; the blank mark sits under the name, so filling an item never moves the other rows. -->
<div class="pick-form">
  <span class="name">{t('pfSpecies')}{#if !value.species}<span class="missing">{t('pfMissing')}</span>{/if}</span>
  <div class="controls">
    {#if value.species}<strong>{name(value.species)}</strong>{/if}
    {#if editing!=='species'}<button type="button" class="link" onclick={()=>editing='species'}>{t(value.species?'pfChange':'pfEnter')}</button>
      {#if !value.species&&recent.length}<span class="muted">{t('ePfRecent')}</span>
        {#each recent as s (s.scientific_name)}<Button size="sm" onclick={()=>change({species:s})}>{name(s)}</Button>{/each}{/if}{/if}
  </div>
  {#if editing==='species'}<div class="search wide" use:focusInput>
    <SpeciesPicker bind:value={()=>null,(row:Species|null)=>{if(row){editing=null;change({species:named(row)});}}} label={t('pfSpeciesName')}/>
    <Button size="sm" onclick={()=>editing=null}>{t('mCancel')}</Button></div>{/if}
  <span class="name">{t('pfVoice')}</span>
  <div class="controls"><span class="segmented" role="group" aria-label={t('pfVoice')}>{#each TYPES as [type,key] (type)}
    <button type="button" aria-pressed={value.sound_type===type} onclick={()=>change({sound_type:type})}>{t(key)}</button>{/each}</span></div>
  <span class="name">{t('pfQuality')}{#if !value.quality}<span class="missing">{t('pfMissing')}</span>{/if}</span>
  <div class="controls">
    <!-- Pressing the chosen grade again clears it: a grade nobody chose is left blank rather than guessed. -->
    <span class="segmented" role="group" aria-label={t('pfQuality')}>{#each QUALITY as grade (grade)}
      <button type="button" aria-pressed={value.quality===grade} onclick={()=>change({quality:value.quality===grade?'':grade})}>{grade}</button>{/each}</span>
    <button type="button" class="link" aria-expanded={guide} onclick={()=>guide=!guide}>{t('pfGuide')}</button>
  </div>
  {#if guide}<div class="guide wide"><dl>{#each QUALITY as grade (grade)}<dt>{grade}</dt><dd>{t(`pfQ${grade}`)}</dd>{/each}</dl><p class="muted">{t('pfGuideNote')}</p></div>{/if}
  <span class="name">{t('pfOthers')}</span>
  <div class="controls">
    <span class="segmented" role="group" aria-label={t('pfOthers')}>
      <button type="button" aria-pressed={!value.others} onclick={()=>{editing=editing==='other'?null:editing;change({others:false,background:[]});}}>{t('pfNo')}</button>
      <button type="button" aria-pressed={value.others} onclick={()=>change({others:true})}>{t('pfYes')}</button></span>
    {#if value.others}
      {#each value.background as other (other.scientific_name)}<span class="tag">{name(other)}<button type="button" class="remove" aria-label={fill('pfRemoveOther',{name:name(other)})}
        onclick={()=>change({background:value.background.filter(b=>b.scientific_name!==other.scientific_name)})}>×</button></span>{/each}
      {#if editing!=='other'&&value.background.length<20}<button type="button" class="link" onclick={()=>editing='other'}>{t('pfAddOther')}</button>{/if}
    {/if}
  </div>
  {#if editing==='other'&&value.others}<div class="search wide" use:focusInput>
    <SpeciesPicker bind:value={()=>null,(row:Species|null)=>{if(row){editing=null;if(!value.background.some(b=>b.scientific_name===row.scientific_name))change({background:[...value.background,named(row)]});}}} label={t('pfOtherName')}/>
    <Button size="sm" onclick={()=>editing=null}>{t('mCancel')}</Button></div>{/if}
</div>
<style>
  .pick-form{display:grid;grid-template-columns:max-content minmax(0,1fr);align-items:center;gap:4px 12px}
  .controls{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px;min-width:0}
  .wide{grid-column:2}
  /* Top-aligned with the first line of buttons, so the blank mark appears below the name without moving it. */
  .name{align-self:start;padding-top:12px;display:flex;flex-direction:column;align-items:flex-start;gap:2px;font-size:14px;font-weight:500;line-height:1.3;color:rgb(var(--stone-800))}
  .link{min-height:44px;padding:0 4px;font-size:14px}
  .tag{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;background:rgb(var(--stone-200));font-size:14px}
  .tag:has(.remove){padding-right:0}
  .remove{min-height:44px;min-width:44px;padding:0;border:0;background:transparent;font-size:18px}
  /* Text, not colour alone, marks what is still blank. */
  .missing{padding:0 4px;border:1px solid rgb(var(--color-warning));border-radius:4px;background:rgb(var(--color-warning-light));color:rgb(var(--stone-800));font-size:12px;font-weight:400}
  .search{display:flex;flex-direction:column;align-items:flex-start;gap:8px}
  .search :global(label){align-self:stretch}
  .guide{padding:8px 12px;border-radius:8px;background:rgb(var(--stone-100));font-size:14px}
  .guide dl{display:grid;grid-template-columns:2em 1fr;gap:2px 8px;margin:0}.guide dt{font-weight:600}.guide dd{margin:0}
</style>
