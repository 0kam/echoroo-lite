<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import { api } from './api.svelte';
  import { app, t } from './app.svelte';
  import { kHz, paint, type Spectrogram, type Tick } from './spectrogram';
  import { scaledZoom, sourceTime } from './annotation-geometry.js';
  import Button from './ui/Button.svelte';
  import ScaleSwitch from './ui/ScaleSwitch.svelte';
  let { mediaPath, start, end, cursor=null, onseek, onplay, storageKey, controls }:
    {mediaPath:string;start:number;end:number;cursor?:number|null;onseek:(time:number)=>void;onplay:()=>void;storageKey?:string;controls?:Snippet}=$props();
  let viewport:HTMLDivElement, track:HTMLDivElement, canvas:HTMLCanvasElement;
  let zoom=$state({x:1,y:1}), left=$state(0), top=$state(0), width=$state(1), height=$state(1), viewWidth=$state(1), viewHeight=$state(1);
  let loading=$state(true), error=$state(''), ticks=$state<Tick[]>([]);
  let alive=true, pan=$state<{x:number;y:number;left:number;top:number}|null>(null);
  const viewStart=$derived(start+left/width*(end-start)),viewEnd=$derived(Math.min(end,start+(left+viewWidth)/width*(end-start)));
  // Ticks follow the rows the sidecar drew (mel or linear): y = (1 − row_fraction) × track height − scroll.
  const visibleTicks=$derived(ticks.map(tick=>({tick,y:(1-tick.row_fraction)*height-top})).filter(({y})=>y>=-.5&&y<=viewHeight+.5));
  const x=(time:number)=>(time-start)/(end-start)*width;
  function measure(){if(!viewport||!track)return;left=viewport.scrollLeft;top=viewport.scrollTop;width=track.clientWidth;height=track.clientHeight;viewWidth=viewport.clientWidth;viewHeight=viewport.clientHeight;}
  function remember(){measure();if(storageKey)try{localStorage.setItem(storageKey,JSON.stringify({...zoom,left:left/width,top:top/height}));}catch{/* View settings are disposable; answers are saved separately. */}}
  async function scale(axis:'x'|'y'|'both',factor:number,event?:WheelEvent){
    const bounds=viewport.getBoundingClientRect();
    const px=event&&axis!=='y'?Math.max(0,Math.min(viewWidth,event.clientX-bounds.left)):viewWidth/2;
    const py=event&&axis!=='x'?Math.max(0,Math.min(viewHeight,event.clientY-bounds.top)):viewHeight/2;
    const fx=(left+px)/width,fy=(top+py)/height;
    zoom=scaledZoom(zoom,axis,factor);await tick();
    viewport.scrollLeft=fx*track.clientWidth-px;viewport.scrollTop=fy*track.clientHeight-py;remember();
  }
  async function reset(){zoom={x:1,y:1};await tick();viewport.scrollTo(0,0);remember();}
  function wheel(axis:'x'|'y'|'both') {return (event:WheelEvent)=>{
    if(event.ctrlKey||event.metaKey||axis==='both'&&Math.abs(event.deltaX)>Math.abs(event.deltaY))return;
    event.preventDefault();const delta=event.deltaY*(event.deltaMode===1?16:event.deltaMode===2?viewHeight:1);
    void scale(axis,Math.exp(-Math.max(-120,Math.min(120,delta))*.004),event);
  };}
  function keyboard(event:KeyboardEvent,axis:'x'|'y'|'both'){
    if(event.ctrlKey||event.metaKey||event.altKey)return;
    if(axis==='both'&&event.key==='Enter'&&!loading&&!error){event.preventDefault();onplay();return;}
    if(axis==='both'&&event.key.startsWith('Arrow')){
      // The view has no scrollbars, so arrow keys pan by a tenth of the visible area.
      event.preventDefault();const dx={ArrowLeft:-1,ArrowRight:1}[event.key]??0,dy={ArrowUp:-1,ArrowDown:1}[event.key]??0;
      viewport.scrollBy(dx*viewWidth/10,dy*viewHeight/10);return;}
    if(['+','=','-','ArrowUp','ArrowDown','ArrowLeft','ArrowRight','Home','End'].includes(event.key)){
      event.preventDefault();void scale(axis,event.key==='Home'?1/8:event.key==='End'?8:['-','ArrowDown','ArrowLeft'].includes(event.key)?1/1.4:1.4);}
  }
  function time(event:MouseEvent){const rect=track.getBoundingClientRect(),bounds=viewport.getBoundingClientRect();return sourceTime(Math.max(bounds.left,Math.min(bounds.left+viewport.clientWidth,event.clientX)),rect.left,rect.width,start,end);}
  onMount(()=>{
    let observer=new ResizeObserver(measure);observer.observe(viewport);measure();
    const restore=async()=>{if(!storageKey)return;try{const view=JSON.parse(localStorage.getItem(storageKey)||'null');
      if(view&&[view.x,view.y,view.left,view.top].every(Number.isFinite)){zoom={x:Math.max(1,Math.min(8,view.x)),y:Math.max(1,Math.min(8,view.y))};await tick();if(!alive)return;viewport.scrollLeft=view.left*track.clientWidth;viewport.scrollTop=view.top*track.clientHeight;measure();}}catch{/* Ignore invalid view settings. */}};
    void restore();
    // The parent re-creates this view when the mel/linear choice changes.
    api<Spectrogram>(`${mediaPath}/spectrogram?start_s=${start}&end_s=${end}&detail=true&scale=${app.frequencyScale}`).then(data=>{
      if(!alive)return;ticks=data.frequency_ticks;paint(canvas,data);
    }).catch(e=>{if(alive)error=e.message;}).finally(()=>{if(alive)loading=false;});
    return ()=>{alive=false;observer.disconnect();};
  });
  function wheelAction(node:HTMLElement,axis:'x'|'y'|'both'){const listener=wheel(axis);node.addEventListener('wheel',listener,{passive:false});return{destroy(){node.removeEventListener('wheel',listener);}};}
</script>
<div class="zoom-controls">{@render controls?.()}<span class="hint" title={t('aZoomHint')}>{t('aZoomShort')}</span><div class="actions">
  <Button size="sm" aria-label={t('aZoomOut')} title={t('aZoomOut')} disabled={Math.min(zoom.x,zoom.y)<=1} onclick={()=>scale('both',1/1.4)}>−</Button>
  <Button size="sm" aria-label={t('aZoomIn')} title={t('aZoomIn')} disabled={Math.max(zoom.x,zoom.y)>=8} onclick={()=>scale('both',1.4)}>＋</Button>
  <Button size="sm" disabled={zoom.x===1&&zoom.y===1} onclick={reset}>{t('aFit')}</Button><ScaleSwitch/></div></div>
<div class="grid">
  <div class="frequency" role="slider" tabindex="0" aria-orientation="vertical" aria-valuemin="1" aria-valuemax="8" aria-valuenow={zoom.y} aria-valuetext={`${zoom.y.toFixed(1)}×`} aria-label={t('aFrequencyAxis')} title={t('aAxisHelp')} use:wheelAction={'y'} onkeydown={e=>keyboard(e,'y')}>
    {#each visibleTicks as {tick,y} (tick.frequency_hz)}<span style:top={`${y}px`} class:edge-high={y<8} class:edge-low={y>viewHeight-8}>{kHz(tick)}</span>{/each}
  </div>
  <!-- Drag or arrow keys pan the view; +/- zoom. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="viewport" bind:this={viewport} onscroll={remember} use:wheelAction={'both'} role="group" tabindex="0" aria-label={t('spectrogram')} onkeydown={e=>keyboard(e,'both')}>
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div class="track" class:panning={!!pan} bind:this={track} style:width={`${zoom.x*100}%`} style:height={`${zoom.y*100}%`} role="group" aria-label={t('spectrogram')}
      ondblclick={e=>{if(loading||error)return;e.preventDefault();const from=time(e);if(from<end)onseek(from);}}
      onpointerdown={e=>{if(loading||error||e.button!==0||e.detail>1)return;e.currentTarget.setPointerCapture(e.pointerId);
        pan={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};}}
      onpointermove={e=>{if(pan)viewport.scrollTo(pan.left-(e.clientX-pan.x),pan.top-(e.clientY-pan.y));}}
      onpointerup={()=>{pan=null;}} onpointercancel={()=>{pan=null;}}>
      <canvas bind:this={canvas}></canvas>
      {#each ticks as tick}{#if tick.row_fraction>0&&tick.row_fraction<1}<div class="guide" style:bottom={`${tick.row_fraction*100}%`}></div>{/if}{/each}
      <svg class="marks" width={width} height={height} aria-hidden="true">
        {#if cursor!==null&&cursor>=start&&cursor<=end}<path d={`M${x(cursor)},0 V${height}`} stroke="#60dcff" stroke-width="2"/>{/if}
      </svg>
    </div>
    {#if loading||error}<div class="state" role="status">{t(error||'loadingSpectrogram')}</div>{/if}
  </div>
  <span class="unit">kHz</span><div class="time-axis" role="slider" tabindex="0" aria-orientation="horizontal" aria-valuemin="1" aria-valuemax="8" aria-valuenow={zoom.x} aria-valuetext={`${zoom.x.toFixed(1)}×`} aria-label={t('aTimeAxis')} title={t('aAxisHelp')} use:wheelAction={'x'} onkeydown={e=>keyboard(e,'x')}>
    <span>{(viewStart-start).toFixed(2)} s</span><span>{((viewStart+viewEnd)/2-start).toFixed(2)} s</span><span>{(viewEnd-start).toFixed(2)} s</span>
  </div>
</div>
<style>
  .zoom-controls{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;font-size:14px}.zoom-controls .actions{gap:4px}
  .hint{flex:1;min-width:200px;color:rgb(var(--stone-600))}
  /* Touch screens have no wheel or double-click; the full help stays in the title. */
  @media (max-width:700px){.hint{display:none}}
  /* In a narrow column the hint takes its own short line instead of pushing the buttons onto two rows. */
  @container (max-width:640px){.hint{order:-1;flex-basis:100%}}
  /* Play, zoom and mel|linear share one row, so the answer bar below never covers the figure (A6). */
  .unit{align-self:center;padding-right:8px;text-align:right;font-size:12px;color:rgb(var(--stone-600))}
  .grid{display:grid;grid-template-columns:54px minmax(0,1fr);gap:4px}
  .frequency{position:relative;cursor:ns-resize;font-size:12px;line-height:1;color:rgb(var(--stone-600));overflow:hidden}
  .frequency span{position:absolute;right:8px;transform:translateY(-50%);font-variant-numeric:tabular-nums}.frequency .edge-high{transform:none}.frequency .edge-low{transform:translateY(-100%)}
  .viewport{height:var(--spec-lg);overflow:hidden;position:relative;background:#050505;overscroll-behavior:contain}
  .track{position:relative;touch-action:none;cursor:grab;min-height:100%;user-select:none}.track.panning{cursor:grabbing}
  canvas{width:100%;height:100%;display:block}.marks{position:absolute;inset:0;pointer-events:none}
  .guide{position:absolute;left:0;right:0;height:1px;background:rgb(255 255 255 / .15);pointer-events:none}
  .state{position:sticky;bottom:0;left:0;background:#101010dd;color:white;padding:12px}
  .time-axis{display:flex;justify-content:space-between;align-items:center;min-height:44px;cursor:ew-resize;font-size:13px;font-variant-numeric:tabular-nums}
</style>
