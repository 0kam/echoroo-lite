<script lang="ts">
  import { onMount, tick, type Snippet } from 'svelte';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import { roseus } from './roseus';
  import { scaledZoom, sourceTime } from './annotation-geometry.js';
  import type { CallRange } from './annotations';
  import Button from './ui/Button.svelte';
  let { mediaPath, start, end, marks=[], cursor=null, onrange, marking=$bindable(false), onseek, onplay, storageKey, controls }:
    {mediaPath:string;start:number;end:number;marks?:CallRange[];cursor?:number|null;onrange?:(a:number,b:number)=>void;marking?:boolean;
     onseek:(time:number)=>void;onplay:()=>void;storageKey?:string;controls?:Snippet}=$props();
  let viewport:HTMLDivElement, track:HTMLDivElement, canvas:HTMLCanvasElement;
  let zoom=$state({x:1,y:1}), left=$state(0), top=$state(0), width=$state(1), height=$state(1), viewWidth=$state(1), viewHeight=$state(1);
  let loading=$state(true), error=$state(''), maxHz=$state(12000), anchor=$state<number|null>(null), draft=$state<number|null>(null);
  let alive=true, anchorX=0, pan=$state<{x:number;y:number;left:number;top:number}|null>(null);
  const viewStart=$derived(start+left/width*(end-start)),viewEnd=$derived(Math.min(end,start+(left+viewWidth)/width*(end-start)));
  const high=$derived(maxHz*(1-top/height)),low=$derived(Math.max(0,maxHz*(1-(top+viewHeight)/height)));
  const x=(time:number)=>(time-start)/(end-start)*width;
  function measure(){if(!viewport||!track)return;left=viewport.scrollLeft;top=viewport.scrollTop;width=track.clientWidth;height=track.clientHeight;viewWidth=viewport.clientWidth;viewHeight=viewport.clientHeight;}
  function remember(){measure();if(storageKey)try{localStorage.setItem(storageKey,JSON.stringify({...zoom,left:left/width,top:top/height}));}catch{/* View settings are disposable; answers are saved separately. */}}
  async function scale(axis:'x'|'y'|'both',factor:number,event?:WheelEvent){
    if(anchor!==null)return;
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
    api<{width:number;height:number;pixels:string;frequency_max_hz:number}>(`${mediaPath}/spectrogram?start_s=${start}&end_s=${end}&detail=true`).then(data=>{
      if(!alive)return;canvas.width=data.width;canvas.height=data.height;maxHz=data.frequency_max_hz;
      const pixels=atob(data.pixels),frame=new ImageData(data.width,data.height);
      for(let i=0;i<pixels.length;i++){const c=pixels.charCodeAt(i)*3;frame.data.set([roseus[c],roseus[c+1],roseus[c+2],255],i*4);}
      canvas.getContext('2d')!.putImageData(frame,0,0);
    }).catch(e=>{if(alive)error=e.message;}).finally(()=>{if(alive)loading=false;});
    return ()=>{alive=false;observer.disconnect();};
  });
  function wheelAction(node:HTMLElement,axis:'x'|'y'|'both'){const listener=wheel(axis);node.addEventListener('wheel',listener,{passive:false});return{destroy(){node.removeEventListener('wheel',listener);}};}
</script>
<div class="zoom-controls">{@render controls?.()}<span title={t('aZoomHint')}>{t(onrange&&marking?'aZoomShortMark':'aZoomShort')}</span><div class="actions">
  {#if onrange}<Button active={marking} aria-pressed={marking} onclick={()=>marking=!marking}>{t('aMarkMode')}</Button>{/if}
  <Button aria-label={t('aZoomOut')} disabled={Math.min(zoom.x,zoom.y)<=1} onclick={()=>scale('both',1/1.4)}>−</Button>
  <Button aria-label={t('aZoomIn')} disabled={Math.max(zoom.x,zoom.y)>=8} onclick={()=>scale('both',1.4)}>＋</Button>
  <Button disabled={zoom.x===1&&zoom.y===1} onclick={reset}>{t('aFit')}</Button></div></div>
<div class="grid">
  <div class="frequency" role="slider" tabindex="0" aria-orientation="vertical" aria-valuemin="1" aria-valuemax="8" aria-valuenow={zoom.y} aria-valuetext={`${zoom.y.toFixed(1)}×`} aria-label={t('aFrequencyAxis')} title={t('aAxisHelp')} use:wheelAction={'y'} onkeydown={e=>keyboard(e,'y')}>
    <span>{+(high/1000).toFixed(2)}</span><span>kHz</span><span>{+(low/1000).toFixed(2)}</span>
  </div>
  <!-- Drag or arrow keys pan the view; +/- zoom. -->
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="viewport" bind:this={viewport} onscroll={remember} use:wheelAction={'both'} role="group" tabindex="0" aria-label={t('spectrogram')} onkeydown={e=>keyboard(e,'both')}>
    <!-- Dragging has an equivalent form for entering exact seconds in RangeEditor. -->
    <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
    <div class="track" class:marking class:panning={!!pan} bind:this={track} style:width={`${zoom.x*100}%`} style:height={`${zoom.y*100}%`} role="group" aria-label={t('aMarkRange')}
      ondblclick={e=>{if(loading||error)return;e.preventDefault();const from=time(e);if(from<end)onseek(from);}}
      onpointerdown={e=>{if(loading||error||e.button!==0||e.detail>1)return;e.currentTarget.setPointerCapture(e.pointerId);
        if(!(onrange&&marking)){pan={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};return;}
        anchorX=e.clientX;anchor=time(e);draft=anchor;}}
      onpointermove={e=>{if(pan)viewport.scrollTo(pan.left-(e.clientX-pan.x),pan.top-(e.clientY-pan.y));else if(anchor!==null)draft=time(e);}}
      onpointerup={e=>{if(anchor!==null&&Math.abs(e.clientX-anchorX)>=4){const end=time(e),from=Math.min(anchor,end),to=Math.max(anchor,end);if(to-from>=.02)onrange?.(from,to);}anchor=null;draft=null;pan=null;}}
      onpointercancel={()=>{anchor=null;draft=null;pan=null;}}>
      <canvas bind:this={canvas}></canvas>
      <svg class="marks" width={width} height={height} aria-hidden="true">
        {#each marks as mark,index}<path d={`M${x(mark.start_s)},${height} V${top+24} H${x(mark.end_s)} V${height}`} stroke="#fff5bd" fill="none" stroke-width="2"/><text x={x(mark.start_s)+4} y={top+19}>{index+1}</text>{/each}
        {#if anchor!==null&&draft!==null}<path d={`M${x(anchor)},0 V${height} M${x(draft)},0 V${height}`} stroke="#fff5bd" stroke-width="2"/>{/if}
        {#if cursor!==null&&cursor>=start&&cursor<=end}<path d={`M${x(cursor)},0 V${height}`} stroke="#60dcff" stroke-width="2"/>{/if}
      </svg>
    </div>
    {#if loading||error}<div class="state" role="status">{t(error||'loadingSpectrogram')}</div>{/if}
  </div>
  <div></div><div class="time-axis" role="slider" tabindex="0" aria-orientation="horizontal" aria-valuemin="1" aria-valuemax="8" aria-valuenow={zoom.x} aria-valuetext={`${zoom.x.toFixed(1)}×`} aria-label={t('aTimeAxis')} title={t('aAxisHelp')} use:wheelAction={'x'} onkeydown={e=>keyboard(e,'x')}>
    <span>{(viewStart-start).toFixed(2)} s</span><span>{((viewStart+viewEnd)/2-start).toFixed(2)} s</span><span>{(viewEnd-start).toFixed(2)} s</span>
  </div>
</div>
<style>
  .zoom-controls{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 12px;font-size:14px}.zoom-controls .actions{gap:6px}.grid{display:grid;grid-template-columns:54px minmax(0,1fr);gap:4px}.frequency{display:flex;flex-direction:column;justify-content:space-between;text-align:right;padding:4px 8px 4px 0;cursor:ns-resize;font-size:12px}.viewport{height:clamp(240px,calc(100vh - 520px),560px);overflow:hidden;position:relative;background:#050505;overscroll-behavior:contain}.track{position:relative;touch-action:none;cursor:grab;min-height:100%;user-select:none}.track.panning{cursor:grabbing}.track.marking{cursor:crosshair}canvas{width:100%;height:100%;display:block}.marks{position:absolute;inset:0;pointer-events:none}text{font:13px sans-serif;fill:#fff5bd;stroke:#121212;stroke-width:3;paint-order:stroke}.state{position:sticky;bottom:0;left:0;background:#101010dd;color:white;padding:12px}.time-axis{display:flex;justify-content:space-between;align-items:center;min-height:44px;cursor:ew-resize;font-size:13px;font-variant-numeric:tabular-nums}
</style>
