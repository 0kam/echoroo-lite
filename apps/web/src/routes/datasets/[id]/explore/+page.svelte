<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { tick, untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,datasetOf,refreshCatalog } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { fill,modelName } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Segment={segment_id:number;recording_id:number;path:string;start_s:number;end_s:number;recorded_at:string;timezone:string};
  type Cluster={cluster:number;size:number;rep:Segment;score_mean:number|null;top_species:{id:string;scientific_name:string}|null};
  type Status={status:'none'|'building'|'ready'|'stale';created_at:string|null;engine:string;segment_count:number;clusters:Cluster[]};
  type Points={n:number;x:Float32Array;y:Float32Array;cluster:Int32Array;shade:Uint8Array;box:[number,number,number,number]};
  const id=Number(page.params.id),PAGE=20,draftKey=`echoroo-explore:${id}`;
  // Categorical hues stepped for the light and the dark surface. Past eight groups the colours repeat; the list number names each group.
  const LIGHT=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
  const DARK=['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767'];
  const palette=$derived(app.dark?DARK:LIGHT);
  let data=$state<Status|null>(null),error=$state(''),pointsError=$state(''),names=$state<Record<string,Species>>({});
  // One group is open at a time; it is also the one highlighted on the map.
  let selected=$state<number|null>(null),shown=$state(PAGE),members=$state<{total:number;segments:Segment[]}|null>(null),membersError=$state('');
  let watched=$state<number|null>(null),restored=$state<string|null>(null),transformed=$state(false);
  const job=$derived(activeJob());
  const building=$derived(job?.stage.startsWith('explore_')&&datasetOf(job.dataset_id)?.site_id===datasetOf(id)?.site_id?job:null);
  const ended=$derived(app.jobs.find(j=>j.id===watched&&(j.status==='failed'||j.status==='cancelled')));
  const signature=$derived(app.jobs.map(j=>`${j.id}:${j.status}`).join(','));
  const rank=$derived(new Map(data?.clusters.map((c,i)=>[c.cluster,i] as [number,number])));
  $effect(()=>{if(building)watched=building.id;});
  // The catalogue poll refreshes jobs every 2 s; any job change reloads the map state.
  $effect(()=>{void signature;untrack(()=>void load());});

  let request=0;
  async function load(){
    const version=++request;
    try{const next=await api<Status>(`/explore?dataset=${id}`);
      if(version!==request)return;
      const changed=next.created_at!==data?.created_at;data=next;error='';
      if(changed&&next.clusters.length){restore(next);void loadPoints(next);}
      for(const sid of new Set(next.clusters.flatMap(c=>c.top_species?[c.top_species.id]:[])))
        if(!(sid in names))api<Species>(`/species/${encodeURIComponent(sid)}`).then(row=>{names[sid]=row;},()=>{/* The scientific name stays as the fallback. */});
    }catch(e){error=e instanceof Error?e.message:'unknown_error';}
  }
  const build=createTask(async()=>{watched=(await api<{id:number}>('/explore',{dataset_id:id})).id;await refreshCatalog();});
  // Coming back from the search page reopens the same group with as many sounds as before.
  function restore(next:Status){
    const draft=(()=>{try{return JSON.parse(localStorage.getItem(draftKey)||'null');}catch{return null;}})() as {created_at?:string;selected?:number|null;shown?:number;loaded?:number}|null;
    const same=!!draft&&draft.created_at===next.created_at,chosen=same?draft?.selected??null:null;
    shown=same&&draft?.shown?draft.shown:PAGE;selected=null;members=null;
    if(chosen!==null&&next.clusters.some(c=>c.cluster===chosen))void open(chosen,draft?.loaded);
    restored=next.created_at;
  }
  $effect(()=>{const value=JSON.stringify({created_at:data?.created_at,selected,shown,loaded:members?.segments.length??0});
    if(restored&&restored===data?.created_at)try{localStorage.setItem(draftKey,value);}catch{/* Private windows may refuse storage. */}});
  async function open(cluster:number,count=PAGE){
    selected=cluster;members=null;membersError='';
    try{const segments:Segment[]=[];let total=0;
      do{const next=await api<{total:number;segments:Segment[]}>(`/explore/clusters/${cluster}/segments?dataset=${id}&offset=${segments.length}`);
        if(selected!==cluster)return;
        total=next.total;segments.push(...next.segments);if(!next.segments.length)break;
      }while(segments.length<Math.min(count,total));
      members={total,segments};
    }catch(e){if(selected===cluster)membersError=e instanceof Error?e.message:'unknown_error';}
  }
  const more=createTask(async()=>{if(!members||selected===null)return;const cluster=selected;
    const next=await api<{total:number;segments:Segment[]}>(`/explore/clusters/${cluster}/segments?dataset=${id}&offset=${members.segments.length}`);
    if(selected===cluster&&members)members.segments.push(...next.segments);});
  function toggle(cluster:number){if(selected===cluster){selected=null;members=null;}else void open(cluster);}
  async function reveal(cluster:number){
    shown=Math.max(shown,Math.ceil(((rank.get(cluster)??0)+1)/PAGE)*PAGE);
    if(selected!==cluster)void open(cluster);
    await tick();document.getElementById(`group-${cluster}`)?.scrollIntoView({block:'nearest'});
  }
  // One sound at a time: starting a player pauses every other one on the page.
  function solo(event:Event){for(const audio of (event.currentTarget as HTMLElement).querySelectorAll('audio'))if(audio!==event.target)audio.pause();}
  // A fixed range only plays start–end, so the clip end stands in for the recording length.
  const sourceOf=(s:Segment)=>({id:s.recording_id,kind:'recording' as const,path:s.path,seconds:s.end_s});
  const when=(s:Segment)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:s.timezone}).format(new Date(Date.parse(s.recorded_at)+s.start_s*1000));
  const similar=(s:Segment)=>`${resolve(`/datasets/${id}/search`)}?recording=${s.recording_id}&start_s=${s.start_s}&end_s=${s.end_s}`;
  const aiHint=(c:Cluster)=>{const s=c.top_species;if(!s)return t('eNoAiHint');const row=names[s.id];return fill('eAiHint',{name:(row&&commonName(row,app.locale))||s.scientific_name});};
  const made=(at:string|null)=>at?new Intl.DateTimeFormat(app.locale,{dateStyle:'medium'}).format(new Date(`${at.replace(' ','T')}Z`)):'';

  // Scatter: every point is drawn once into an offscreen canvas; pan and zoom move that image and redraw sharply when the gesture pauses.
  let points:Points|null=null,canvas:HTMLCanvasElement|null=null,offscreen:HTMLCanvasElement|null=null,timer:ReturnType<typeof setTimeout>|undefined;
  // Device pixels: screen = base * k + t, where base fits all points into the canvas.
  let view={k:1,tx:0,ty:0},drawn={k:1,tx:0,ty:0},drag:{x:number;y:number;tx:number;ty:number;moved:boolean}|null=null;
  async function loadPoints(status:Status){
    pointsError='';
    try{const buffer=await api<ArrayBuffer>(`/explore/points?dataset=${id}`,undefined,'GET',true);
      if(status.created_at!==data?.created_at)return;
      // uint32 n, float32 x[n], float32 y[n], int32 cluster[n], int64 segment_id[n]; little-endian, the native order of every supported PC.
      const n=new DataView(buffer).getUint32(0,true);
      if(buffer.byteLength!==4+20*n)throw Error('unknown_error');
      const x=new Float32Array(buffer,4,n),y=new Float32Array(buffer,4+4*n,n),cluster=new Int32Array(buffer,4+8*n,n);
      const order=new Map(status.clusters.map((c,i)=>[c.cluster,i] as [number,number]));
      const shade=new Uint8Array(n),box:Points['box']=[Infinity,-Infinity,Infinity,-Infinity];
      for(let i=0;i<n;i++){shade[i]=(order.get(cluster[i])??0)%LIGHT.length;
        box[0]=Math.min(box[0],x[i]);box[1]=Math.max(box[1],x[i]);box[2]=Math.min(box[2],y[i]);box[3]=Math.max(box[3],y[i]);}
      points={n,x,y,cluster,shade,box};reset();
    }catch(e){pointsError=e instanceof Error?e.message:'unknown_error';}
  }
  function frame(target:HTMLCanvasElement,{box:[x0,x1,y0,y1]}:Points){
    const ratio=target.width/Math.max(1,target.clientWidth),pad=8*ratio,{width,height}=target;
    return {ratio,x0,y0,sx:(width-2*pad)/(x1-x0||1)*view.k,sy:(height-2*pad)/(y1-y0||1)*view.k,ox:pad*view.k+view.tx,oy:(height-pad)*view.k+view.ty};
  }
  function render(){
    if(!points||!canvas||!offscreen)return;
    const {n,x,y,cluster,shade}=points,{ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),context=offscreen.getContext('2d')!;
    offscreen.width=canvas.width;offscreen.height=canvas.height;
    const dot=(i:number,size:number)=>context.fillRect(ox+(x[i]-x0)*sx-size/2,oy-(y[i]-y0)*sy-size/2,size,size);
    context.globalAlpha=selected===null?.7:.15;
    for(let c=0;c<palette.length;c++){context.fillStyle=palette[c];for(let i=0;i<n;i++)if(shade[i]===c&&cluster[i]!==selected)dot(i,2*ratio);}
    if(selected!==null){context.globalAlpha=1;context.fillStyle=palette[(rank.get(selected)??0)%palette.length];for(let i=0;i<n;i++)if(cluster[i]===selected)dot(i,3*ratio);}
    drawn={...view};blit();
  }
  function blit(){
    if(!canvas||!offscreen)return;
    const context=canvas.getContext('2d')!,r=view.k/drawn.k;
    context.setTransform(1,0,0,1,0,0);context.clearRect(0,0,canvas.width,canvas.height);
    context.setTransform(r,0,0,r,view.tx-drawn.tx*r,view.ty-drawn.ty*r);context.drawImage(offscreen,0,0);
  }
  function moved(){transformed=true;blit();clearTimeout(timer);timer=setTimeout(render,150);}
  function reset(){view={k:1,tx:0,ty:0};transformed=false;render();}
  $effect(()=>{void selected;void palette;untrack(render);});
  function scatter(node:HTMLCanvasElement){
    canvas=node;offscreen=document.createElement('canvas');
    const resize=new ResizeObserver(()=>{const r=devicePixelRatio||1;node.width=Math.round(node.clientWidth*r);node.height=Math.round(node.clientHeight*r);render();});
    // Zoom around the cursor; a non-passive listener keeps the page from scrolling instead.
    const wheel=(event:WheelEvent)=>{event.preventDefault();
      const bounds=node.getBoundingClientRect(),r=node.width/bounds.width,cx=(event.clientX-bounds.left)*r,cy=(event.clientY-bounds.top)*r;
      const k=Math.min(64,Math.max(1,view.k*Math.exp(-event.deltaY*.002))),g=k/view.k;
      view={k,tx:cx-(cx-view.tx)*g,ty:cy-(cy-view.ty)*g};moved();};
    resize.observe(node);node.addEventListener('wheel',wheel,{passive:false});
    return {destroy(){resize.disconnect();node.removeEventListener('wheel',wheel);clearTimeout(timer);canvas=null;}};
  }
  function down(event:PointerEvent){if(event.button!==0)return;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,tx:view.tx,ty:view.ty,moved:false};}
  function move(event:PointerEvent){
    if(!drag||!canvas)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    if(!drag.moved&&Math.hypot(dx,dy)<4)return;
    const r=canvas.width/Math.max(1,canvas.clientWidth);drag.moved=true;view={...view,tx:drag.tx+dx*r,ty:drag.ty+dy*r};moved();
  }
  // A click without dragging picks the nearest point within 8 px and opens its group.
  function up(event:PointerEvent){
    const click=drag&&!drag.moved;drag=null;
    if(!click||!points||!canvas)return;
    const {ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),bounds=canvas.getBoundingClientRect();
    const px=(event.clientX-bounds.left)*ratio,py=(event.clientY-bounds.top)*ratio;
    let best=-1,limit=(8*ratio)**2;
    for(let i=0;i<points.n;i++){const dx=ox+(points.x[i]-x0)*sx-px,dy=oy-(points.y[i]-y0)*sy-py,d=dx*dx+dy*dy;if(d<limit){limit=d;best=i;}}
    if(best>=0)void reveal(points.cluster[best]);
  }
</script>
<div class="page wide" onplaycapture={solo}>
  <div class="crumbs"><a href={resolve(`/datasets/${id}`)}>{t('uBackResults')}</a></div>
  <div class="page-header"><div><h1>{t('eTitle')}</h1><p>{t('eLead')}</p></div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if ended}<p class={ended.status==='failed'?'error':'notice'} role="status">{t(ended.status==='cancelled'?'cancelled':ended.error??'explore_failed')}</p>{/if}
  {#if data}
    {#if data.status==='building'||building}<section class="status-box" role="status">
      {#if building}<JobStatus job={building}/>{:else}<p><Spinner/> {t('eBuilding')}</p>{/if}
      <p class="muted">{t('eTakesMinutes')}</p></section>
    {:else if !data.clusters.length}<section class="panel empty">
      <p>{t('eEmpty')}</p><p>{t('eTakesMinutes')}</p>
      <Button variant="primary" pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eBuild')}</Button>
      {#if job&&!build.pending}<p>{t('job_busy')}</p>{/if}
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}</section>
    {:else if data.status==='stale'}<section class="notice stack" role="status">
      <p>{t('eStale')}</p>
      <div class="actions"><Button pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eRebuild')}</Button><span>{t(job&&!build.pending?'job_busy':'eTakesMinutes')}</span></div>
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}</section>{/if}
    {#if data.clusters.length}
      <p class="muted">{fill('eMadeFrom',{date:made(data.created_at),model:modelName(data.engine),n:data.segment_count.toLocaleString(app.locale)})}</p>
      <div class="explore">
        <section class="panel stack map-panel">
          <div class="section-heading"><h2>{t('eMapHeading')}</h2><Button size="sm" disabled={!transformed} onclick={reset}>{t('eReset')}</Button></div>
          {#if pointsError}<p class="error" role="alert">{t(pointsError)}</p>{/if}
          <!-- The group list is the keyboard path to the same groups; the map adds pointer zoom, pan and picking. -->
          <canvas use:scatter aria-label={t('eMapLabel')} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={()=>drag=null}></canvas>
          <p class="muted">{fill('eMapHint',{s:+(data.clusters[0].rep.end_s-data.clusters[0].rep.start_s).toFixed(1)})}</p>
        </section>
        <section class="panel stack">
          <h2>{t('eListHeading')}</h2>
          <p class="muted">{t('eListHint')} {t('localTime')}</p>
          {#each data.clusters.slice(0,shown) as c,index (c.cluster)}{@const expanded=selected===c.cluster}
            <article class="group" class:open={expanded} id={`group-${c.cluster}`}>
              <h3><button class="group-toggle" aria-expanded={expanded} onclick={()=>toggle(c.cluster)}>
                <span class="swatch" style:background={palette[index%palette.length]} aria-hidden="true"></span>
                <span>{fill('eGroup',{n:index+1})}</span><span class="muted">{fill('eSize',{n:c.size.toLocaleString(app.locale)})}</span>
                <span class="chevron" aria-hidden="true">{expanded?'▲':'▼'}</span></button></h3>
              <p class="muted">{aiHint(c)}</p>
              <AudioRegion source={sourceOf(c.rep)} fixedRange compact start={c.rep.start_s} end={c.rep.end_s}/>
              {#if expanded}<div class="members">
                <p class="muted">{t('eMembersHint')}</p>
                {#if membersError}<p class="error" role="alert">{t(membersError)}</p>
                {:else if !members}<p role="status"><Spinner/> {t('loading')}</p>
                {:else}
                  {#each members.segments as s (s.segment_id)}<div class="member"><span>{when(s)}</span>
                    <AudioRegion source={sourceOf(s)} fixedRange compact start={s.start_s} end={s.end_s}>
                      {#snippet actions()}<Button size="sm" href={similar(s)}>{t('eFindSimilar')}</Button>{/snippet}
                    </AudioRegion></div>{/each}
                  {#if more.error}<p class="error" role="alert">{t(more.error)}</p>{/if}
                  {#if members.segments.length<members.total}<Button pending={more.pending} onclick={()=>more.run()}>{t('eMore')}</Button>{/if}
                {/if}</div>{/if}
            </article>{/each}
          {#if shown<data.clusters.length}<Button onclick={()=>shown+=PAGE}>{t('eMoreGroups')}</Button>{/if}
        </section>
      </div>
    {/if}
  {:else if !error}<p role="status"><Spinner/> {t('eLoading')}</p>{/if}
</div>
<style>.explore{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
  .map-panel{position:sticky;top:16px}
  canvas{display:block;width:100%;height:420px;border:1px solid rgb(var(--color-card-border));border-radius:8px;background:rgb(var(--stone-50));touch-action:none;cursor:grab}
  .group{display:flex;flex-direction:column;gap:8px;padding:8px 12px 12px;border:1px solid rgb(var(--color-card-border));border-radius:8px}
  .group.open{border-left:4px solid rgb(var(--primary-700))}
  .group-toggle{width:100%;justify-content:flex-start;flex-wrap:wrap;gap:4px 12px;padding:8px 4px;border:0;background:transparent;font-size:18px;font-weight:600;text-align:left}
  .group-toggle:hover{background:rgb(var(--stone-200))}.group-toggle .muted{font-weight:400}.chevron{margin-left:auto;font-size:14px}
  .swatch{width:14px;height:14px;border-radius:4px;flex:none}
  .members{display:flex;flex-direction:column;gap:8px}
  .member{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .empty :global(.button){align-self:center}.members>:global(.button){align-self:flex-start}
  @media(max-width:900px){.explore{grid-template-columns:minmax(0,1fr)}.map-panel{position:static}canvas{height:320px}}</style>
