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
  let { value, group=null, name, onchange }:{value:Label;group?:Named|null;name:(s:Named)=>string;onchange:(label:Label)=>void}=$props();
  let editing=$state<'species'|'other'|null>(null),guide=$state(false);
  const same=(a:Named|null,b:Named|null)=>!!a&&!!b&&a.scientific_name===b.scientific_name;
  const named=(row:Species):Named=>({id:row.id,scientific_name:row.scientific_name});
  function change(patch:Partial<Label>){
    const next={...value,...patch};
    if(JSON.stringify([next.species,next.background,next.sound_type,next.quality,next.others])!==JSON.stringify([value.species,value.background,value.sound_type,value.quality,value.others]))onchange(next);
  }
  const TYPES=[['song','pfSong'],['call','pfCall'],['','pfUnknown']] as const;
  const QUALITY=['A','B','C','D','E'] as const;
</script>
<div class="pick-form">
  <div class="field"><span class="name">{t('pfSpecies')}</span>
    {#if value.species}<strong>{name(value.species)}</strong>{#if same(value.species,group)}<span class="tag">{t('pfGroupMark')}</span>{/if}
    {:else}<span class="missing">{t('pfMissing')}</span>{/if}
    {#if editing!=='species'}<button type="button" class="link" onclick={()=>editing='species'}>{t(value.species?'pfChange':'pfEnter')}</button>{/if}
    {#if group&&!same(value.species,group)}<button type="button" class="link" onclick={()=>change({species:group})}>{t('pfFollowGroup')}</button>{/if}
  </div>
  {#if editing==='species'}<div class="search" use:focusInput>
    <SpeciesPicker bind:value={()=>null,(row:Species|null)=>{if(row){editing=null;change({species:named(row)});}}} label={t('pfSpeciesName')}/>
    <Button size="sm" onclick={()=>editing=null}>{t('mCancel')}</Button></div>{/if}
  <div class="field"><span class="name">{t('pfVoice')}</span>
    <span class="segmented" role="group" aria-label={t('pfVoice')}>{#each TYPES as [type,key] (type)}
      <button type="button" aria-pressed={value.sound_type===type} onclick={()=>change({sound_type:type})}>{t(key)}</button>{/each}</span>
  </div>
  <div class="field"><span class="name">{t('pfQuality')}</span>
    <!-- Pressing the chosen grade again clears it: a grade nobody chose is left blank rather than guessed. -->
    <span class="segmented" role="group" aria-label={t('pfQuality')}>{#each QUALITY as grade (grade)}
      <button type="button" aria-pressed={value.quality===grade} onclick={()=>change({quality:value.quality===grade?'':grade})}>{grade}</button>{/each}</span>
    <button type="button" class="link" aria-expanded={guide} onclick={()=>guide=!guide}>{t('pfGuide')}</button>
    {#if !value.quality}<span class="missing">{t('pfMissing')}</span>{/if}
  </div>
  {#if guide}<div class="guide"><dl>{#each QUALITY as grade (grade)}<dt>{grade}</dt><dd>{t(`pfQ${grade}`)}</dd>{/each}</dl><p class="muted">{t('pfGuideNote')}</p></div>{/if}
  <div class="field"><span class="name">{t('pfOthers')}</span>
    <span class="segmented" role="group" aria-label={t('pfOthers')}>
      <button type="button" aria-pressed={!value.others} onclick={()=>{editing=editing==='other'?null:editing;change({others:false,background:[]});}}>{t('pfNo')}</button>
      <button type="button" aria-pressed={value.others} onclick={()=>change({others:true})}>{t('pfYes')}</button></span>
    {#if value.others}
      {#each value.background as other (other.scientific_name)}<span class="tag">{name(other)}<button type="button" class="remove" aria-label={fill('pfRemoveOther',{name:name(other)})}
        onclick={()=>change({background:value.background.filter(b=>b.scientific_name!==other.scientific_name)})}>×</button></span>{/each}
      {#if editing!=='other'&&value.background.length<20}<button type="button" class="link" onclick={()=>editing='other'}>{t('pfAddOther')}</button>{/if}
    {/if}
  </div>
  {#if editing==='other'&&value.others}<div class="search" use:focusInput>
    <SpeciesPicker bind:value={()=>null,(row:Species|null)=>{if(row){editing=null;if(!value.background.some(b=>b.scientific_name===row.scientific_name))change({background:[...value.background,named(row)]});}}} label={t('pfOtherName')}/>
    <Button size="sm" onclick={()=>editing=null}>{t('mCancel')}</Button></div>{/if}
</div>
<style>
  .pick-form{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}
  .field{display:flex;flex-wrap:wrap;align-items:center;gap:4px 8px}
  .name{font-size:14px;font-weight:500;color:rgb(var(--stone-800))}
  .link{min-height:44px;padding:0 4px;font-size:14px}
  .tag{display:inline-flex;align-items:center;padding:2px 10px;border-radius:999px;background:rgb(var(--stone-200));font-size:14px}
  .tag:has(.remove){padding-right:0}
  .remove{min-height:44px;min-width:44px;padding:0;border:0;background:transparent;font-size:18px}
  /* Text, not colour alone, marks what is still blank. */
  .missing{padding:0 8px;border:1px solid rgb(var(--color-warning));border-radius:4px;background:rgb(var(--color-warning-light));color:rgb(var(--stone-800));font-size:14px}
  .search{flex-basis:100%;display:flex;flex-direction:column;align-items:flex-start;gap:8px}
  .search :global(label){align-self:stretch}
  .guide{flex-basis:100%;padding:8px 12px;border-radius:8px;background:rgb(var(--stone-100));font-size:14px}
  .guide dl{display:grid;grid-template-columns:2em 1fr;gap:2px 8px;margin:0}.guide dt{font-weight:600}.guide dd{margin:0}
</style>
