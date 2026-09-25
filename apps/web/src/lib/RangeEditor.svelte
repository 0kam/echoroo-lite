<script lang="ts">
  import { untrack } from 'svelte';
  import { app,t } from './app.svelte';
  import { commonName,type Species } from './species';
  import type { CallRange } from './annotations';
  import AudioRegion,{type AudioSource} from './AudioRegion.svelte';
  import SpeciesPicker from './SpeciesPicker.svelte';
  import Button from './ui/Button.svelte';
  let {source,start,end,palette=$bindable([]),ranges=$bindable([]),storageKey,target=null,disabled=false,detectionCounts={}}:
    {source:AudioSource;start:number;end:number;palette:Species[];ranges?:CallRange[];storageKey:string;target?:Species|null;disabled?:boolean;detectionCounts?:Record<string,number>}=$props();
  let chosen=$state<string|null|undefined>(untrack(()=>target?.id)),filter=$state(''),adding=$state(false),picked=$state<Species|null>(null);
  let from=$state(0),to=$state(1),player:AudioRegion,error=$state(''),marking=$state(false);
  const species=$derived([...new Map([...palette,...(target?[target]:[])].map(s=>[s.id,s])).values()]);
  const ordered=$derived(species.filter(s=>[commonName(s,app.locale),s.scientific_name,...Object.values(s.common_names)].some(n=>n.toLocaleLowerCase(app.locale).includes(filter.toLocaleLowerCase(app.locale)))).sort((a,b)=>(detectionCounts[b.id]??0)-(detectionCounts[a.id]??0)||(commonName(a,app.locale)||a.scientific_name).localeCompare(commonName(b,app.locale)||b.scientific_name,app.locale)));
  const label=(sid:string|null|undefined)=>sid===null?t('aUnknown'):commonName(species.find(s=>s.id===sid)??{common_names:{}},app.locale)||species.find(s=>s.id===sid)?.scientific_name||'';
  $effect(()=>{if(picked)untrack(()=>{if(!palette.some(s=>s.id===picked!.id))palette=[...palette,picked!];choose(picked!.id);picked=null;adding=false;});});
  // Choosing an animal means the next drag should mark its call rather than move the view.
  function choose(id:string|null){chosen=id;error='';marking=true;}
  function mark(a:number,b:number){if(disabled)return;if(chosen===undefined){error='aChooseFirst';return;}
    if(!(Number.isFinite(a)&&Number.isFinite(b)&&start<=a&&a<b&&b<=end)){error='invalid_interval';return;}
    player?.stop();ranges=[...ranges,{species_id:chosen,start_s:a,end_s:b}];error='';}
  export function stop(){player?.stop();}
</script>
<div class="editor" class:targeted={!!target}>
  {#if !target}<aside>
    <label>{t('aNames')}<input type="search" bind:value={filter} placeholder={t('aFilter')} disabled={disabled}/></label>
    <div class="palette" role="group" aria-label={t('aChoose')}>
      {#each ordered as s}<button type="button" disabled={disabled} aria-pressed={chosen===s.id} onclick={()=>choose(s.id)}>{commonName(s,app.locale)||s.scientific_name}<i>{s.scientific_name}</i></button>{/each}
    </div>
    <Button disabled={disabled} active={chosen===null} aria-pressed={chosen===null} onclick={()=>choose(null)}>{t('aUnknown')}</Button>
    <Button disabled={disabled} onclick={()=>adding=!adding}>{t('aAddSpecies')}</Button>
    {#if adding}<SpeciesPicker bind:value={picked} label={t('aCatalog')} disabled={disabled}/>{/if}
  </aside>{/if}
  <div class="audio-area">
    {#if !target}<p class="instruction">{chosen===undefined?t('aChooseFirst'):t(marking?'aDrag':'aDragOff').replace('{name}',label(chosen))}</p>{/if}
    <AudioRegion bind:this={player} {source} fixedRange zoomable {start} {end} marks={ranges} onrange={mark} bind:marking storageKey={`${storageKey}:view`}/>
    {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
    <!-- When checking one species, marking is optional, so its tools appear only once the user turns it on. -->
    {#if !target||marking||ranges.length}<details class="keyboard"><summary>{t('aKeyboard')}</summary><div class="actions">
      <label>{t('rangeStart')}<input type="number" min="0" max={end-start} step="0.01" bind:value={from}/></label>
      <label>{t('rangeEnd')}<input type="number" min="0" max={end-start} step="0.01" bind:value={to}/></label>
      <Button disabled={disabled||chosen===undefined} onclick={()=>mark(start+from,start+to)}>{t('aAddRange')}</Button></div></details>
    <div class="heard"><h3>{t('aMarks')}</h3>
      {#each ranges as range,index}<div class="range-row"><span>{index+1}. <strong>{label(range.species_id)}</strong></span><span>{(range.start_s-start).toFixed(2)}–{(range.end_s-start).toFixed(2)} s</span>
        <Button disabled={disabled} onclick={()=>player.play(range.start_s,range.end_s)}>{t('uListen')}</Button><Button disabled={disabled} aria-label={`${t('aRemove')} ${index+1}`} onclick={()=>{ranges=ranges.filter((_,i)=>i!==index);}}>{t('aRemove')}</Button></div>
      {:else}<p class="muted">{t(target?'aNoMarksTarget':'aNoMarks')}</p>{/each}
    </div>{/if}
  </div>
</div>
<style>
  .editor{display:grid;grid-template-columns:minmax(0,1fr) 230px;gap:24px}.targeted{grid-template-columns:minmax(0,1fr)}aside{order:2;display:flex;flex-direction:column;gap:10px}.palette{max-height:300px;overflow:auto;border:1px solid rgb(var(--stone-300));border-radius:8px}.palette button{display:block;width:100%;text-align:left;padding:10px 12px;min-height:54px;border:0;border-bottom:1px solid rgb(var(--stone-200));background:transparent;color:inherit;cursor:pointer;font-size:16px}.palette button[aria-pressed=true]{background:rgb(var(--primary-100));outline:2px solid rgb(var(--primary-500));outline-offset:-2px}.palette i{display:block;font-size:12px;opacity:.7}.audio-area{min-width:0}.instruction{margin:0 0 12px;min-height:24px;font-weight:600}.range-row{display:flex;align-items:center;flex-wrap:wrap;gap:10px;border-top:1px solid rgb(var(--stone-200));padding:8px 0}.range-row>span:first-child{flex:1}.heard h3{font-size:16px;margin:18px 0 8px}.keyboard{margin-top:10px}.keyboard summary{cursor:pointer;min-height:44px;display:flex;align-items:center}.keyboard label{max-width:140px}.keyboard .actions{align-items:end}@media(max-width:860px){.editor{grid-template-columns:minmax(0,1fr)}.palette{max-height:160px}}
</style>
