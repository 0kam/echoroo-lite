<script lang="ts">
  import { app,t } from './app.svelte';
  import { commonName,type Species } from './species';
  import type { CallRange } from './annotations';
  import AudioRegion,{type AudioSource} from './AudioRegion.svelte';
  import Button from './ui/Button.svelte';
  // Marks where one species calls; `ranges` holds only that species' marks.
  let {source,start,end,ranges=$bindable([]),storageKey,target,disabled=false}:
    {source:AudioSource;start:number;end:number;ranges?:CallRange[];storageKey:string;target:Species;disabled?:boolean}=$props();
  // Marking is this editor's main task, so dragging marks by default; the toggle switches it to moving the view.
  let from=$state(0),to=$state(1),player:AudioRegion,error=$state(''),marking=$state(true);
  function mark(a:number,b:number){if(disabled)return;
    if(!(Number.isFinite(a)&&Number.isFinite(b)&&start<=a&&a<b&&b<=end)){error='invalid_interval';return;}
    player?.stop();ranges=[...ranges,{species_id:target.id,start_s:a,end_s:b}];error='';}
  export function stop(){player?.stop();}
</script>
<div class="audio-area">
  <p class="instruction">{t(marking?'aDrag':'aDragOff').replace('{name}',commonName(target,app.locale)||target.scientific_name)}</p>
  <AudioRegion bind:this={player} {source} fixedRange zoomable {start} {end} marks={ranges} onrange={mark} bind:marking storageKey={`${storageKey}:view`}/>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  <details class="keyboard"><summary>{t('aKeyboard')}</summary><div class="actions">
    <label>{t('rangeStart')}<input type="number" min="0" max={end-start} step="0.01" bind:value={from}/></label>
    <label>{t('rangeEnd')}<input type="number" min="0" max={end-start} step="0.01" bind:value={to}/></label>
    <Button {disabled} onclick={()=>mark(start+from,start+to)}>{t('aAddRange')}</Button></div></details>
  {#each ranges as range,index}<div class="range-row"><span>{index+1}. {(range.start_s-start).toFixed(2)}–{(range.end_s-start).toFixed(2)} s</span>
    <Button {disabled} onclick={()=>player.play(range.start_s,range.end_s)}>{t('uListen')}</Button><Button {disabled} aria-label={`${t('aRemove')} ${index+1}`} onclick={()=>{ranges=ranges.filter((_,i)=>i!==index);}}>{t('aRemove')}</Button></div>
  {:else}<p class="muted">{t('aNoMarks')}</p>{/each}
</div>
<style>
  .audio-area{min-width:0}.instruction{margin:0 0 12px;min-height:24px;font-weight:600}.range-row{display:flex;align-items:center;flex-wrap:wrap;gap:10px;border-top:1px solid rgb(var(--stone-200));padding:8px 0}.range-row>span:first-child{flex:1}.keyboard{margin-top:10px}.keyboard summary{cursor:pointer;min-height:44px;display:flex;align-items:center}.keyboard label{max-width:140px}.keyboard .actions{align-items:end}
</style>
