<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,datasetOf,refreshCatalog } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { fill,modelName } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  // `id` is null when the species list has no row for the scientific name.
  type Named={id:string|null;scientific_name:string};
  type Segment={segment_id:number;recording_id:number;path:string;start_s:number;end_s:number;recorded_at:string;timezone:string;candidates:(Named&{score:number})[]};
  // Points come in tree order, so the sounds of every group are the positions lo..hi-1 of the point arrays.
  type Group={node:number;name:string;size:number;lo:number;hi:number;child_count:number;rep:{start_s:number;end_s:number};hint_rate:number;top_species:(Named&{share:number})[]};
  type Status={status:'none'|'building'|'ready'|'stale';created_at:string|null;engine:string;segment_count:number;node?:Group;path?:{node:number;name:string}[];children?:Group[]};
  type Page={total:number;segments:Segment[]};
  type List=Page&{pending:boolean;error:string};
  type Points={n:number;x:Float32Array;y:Float32Array;box:[number,number,number,number]};
  const id=Number(page.params.id),PAGE=20,MAX_ZOOM=1000,draftKey=`echoroo-explore:${id}`;
  // Categorical hues stepped for the light and the dark surface; a group has at most eight smaller groups, one colour each.
  const LIGHT=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
  const DARK=['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767'];
  const palette=$derived(app.dark?DARK:LIGHT);
  // The group being viewed lives in the URL (`?node=`), so the browser's back button goes up one level.
  const nodeId=$derived(Number(new URL(page.url.hash.slice(1),page.url).searchParams.get('node'))||0);
  const here=(node:number)=>`${resolve(`/datasets/${id}/explore`)}?node=${node}`;
  // Coming back without `?node=` (the search page's back link) reopens the same group, the same rows and as many sounds as before.
  let draft=(()=>{try{return JSON.parse(localStorage.getItem(draftKey)||'null');}catch{return null;}})() as {created_at?:string;node?:number;loaded?:Record<string,number>}|null;
  let restoring=!new URL(page.url.hash.slice(1),page.url).searchParams.has('node')&&!!draft;
  let data=$state<Status|null>(null),error=$state(''),pointsError=$state(''),names=$state<Record<string,Species>>({});
  // The child picked on the map or opened in the list (its index): its row is highlighted and the other children are dimmed.
  let selected=$state<number|null>(null);
  // Open rows and the loaded sounds of each group, by group number; the sounds stay cached while the same map is shown.
  let open=$state<Record<number,boolean>>({}),lists=$state<Record<number,List>>({});
  // The sound picked on the map: its position in the point arrays, then its details.
  let pickAt=$state(-1),pick=$state<Segment|null>(null),pickError=$state('');
  let watched=$state<number|null>(null),transformed=$state(false);
  const node=$derived(data?.node),children=$derived(data?.children??[]),trail=$derived(data?.path??[]),parent=$derived(trail.at(-2)?.node);
  const job=$derived(activeJob());
  const building=$derived(job?.stage.startsWith('explore_')&&datasetOf(job.dataset_id)?.site_id===datasetOf(id)?.site_id?job:null);
  const ended=$derived(app.jobs.find(j=>j.id===watched&&(j.status==='failed'||j.status==='cancelled')));
  const signature=$derived(app.jobs.map(j=>`${j.id}:${j.status}`).join(','));
  $effect(()=>{if(building)watched=building.id;});
  // The catalogue poll refreshes jobs every 2 s; any job change or a move to another group reloads the map state.
  $effect(()=>{void signature;void nodeId;untrack(()=>void load());});

  let request=0,quietUntil=0,lastClick=0;
  async function load(){
    const version=++request,wanted=nodeId;
    try{const next=await api<Status>(`/explore?dataset=${id}&node=${wanted}`);
      if(version!==request)return;
      if(restoring){restoring=false;if(next.created_at===draft?.created_at&&draft?.node&&draft.node!==wanted){void go(draft.node,true);return;}}
      const before=data?.node,fresh=next.created_at!==data?.created_at,moved=next.node?.node!==before?.node;
      data=next;error='';
      if(fresh){points=null;lists={};if(next.node)void loadPoints(next);}
      if(!next.node)return;
      if(fresh||moved){
        selected=null;open={};pickAt=-1;pick=null;quietUntil=performance.now()+400;
        const saved=draft?.created_at===next.created_at&&draft?.node===next.node.node?draft.loaded??{}:{};draft=null;
        // A group that is not divided further shows its own sounds instead of rows.
        if(!next.children?.length)void loadList(next.node.node,saved[next.node.node]);
        for(const c of next.children??[])if(saved[c.node]){open[c.node]=true;void loadList(c.node,saved[c.node]);}
        fly(!!before);
      }
      askNames([next.node,...next.children??[]].flatMap(g=>g.top_species));
    }catch(e){if(version!==request)return;const code=e instanceof Error?e.message:'unknown_error';
      // A group of an older map no longer exists: start again from the whole map.
      if(code==='not_found'&&wanted){void go(0,true);return;}
      error=code;}
  }
  function go(target:number,replaceState=false){clearTimeout(scrollTimer);quietUntil=performance.now()+400;return goto(here(target),{replaceState,noScroll:true});}
  // Group numbers change when the map is made again, so a new map starts from the whole map.
  const build=createTask(async()=>{watched=(await api<{id:number}>('/explore',{dataset_id:id})).id;await refreshCatalog();if(nodeId)void go(0,true);});
  // Loads a group's sounds, nearest to its centre first, until `count` are loaded or none are left.
  async function loadList(at:number,count=PAGE){
    lists[at]??={total:0,segments:[],pending:false,error:''};
    const list=lists[at];
    if(list.pending||list.segments.length>=Math.max(1,Math.min(count,list.total)))return;
    list.pending=true;list.error='';
    try{do{const next=await api<Page>(`/explore/nodes/${at}/segments?dataset=${id}&offset=${list.segments.length}`);
        list.total=next.total;list.segments.push(...next.segments);askNames(next.segments.flatMap(s=>s.candidates));
        if(!next.segments.length)break;
      }while(list.segments.length<Math.min(count,list.total));
    }catch(e){list.error=e instanceof Error?e.message:'unknown_error';}
    finally{list.pending=false;}
  }
  $effect(()=>{if(!node||!data?.created_at)return;
    const shown=children.length?children.filter(c=>open[c.node]).map(c=>c.node):[node.node];
    const value=JSON.stringify({created_at:data.created_at,node:node.node,loaded:Object.fromEntries(shown.map(at=>[at,lists[at]?.segments.length||PAGE]))});
    try{localStorage.setItem(draftKey,value);}catch{/* Private windows may refuse storage. */}});
  async function choose(at:number){
    pickAt=at;pick=null;pickError='';
    try{const next=await api<Segment>(`/explore/points/${at}?dataset=${id}`);
      if(pickAt===at){pick=next;askNames(next.candidates);}
    }catch(e){if(pickAt===at)pickError=e instanceof Error?e.message:'unknown_error';}
  }
  // The row scrolls into view only after the double-click window: an instant scroll would move the sticky map under a fast second click.
  let scrollTimer:ReturnType<typeof setTimeout>|undefined;
  function reveal(child:number,block:ScrollLogicalPosition='nearest'){selected=child;clearTimeout(scrollTimer);
    scrollTimer=setTimeout(()=>document.getElementById(`group-${children[child]?.node}`)?.scrollIntoView({block,behavior:'smooth'}),360);}
  function escape(event:KeyboardEvent){if(event.key==='Escape'&&!event.defaultPrevented&&parent!==undefined)void go(parent);}
  // One sound at a time: starting a player pauses every other one on the page.
  function solo(event:Event){for(const audio of (event.currentTarget as HTMLElement).querySelectorAll('audio'))if(audio!==event.target)audio.pause();}
  // A fixed range only plays start–end, so the clip end stands in for the recording length.
  const sourceOf=(s:Segment)=>({id:s.recording_id,kind:'recording' as const,path:s.path,seconds:s.end_s});
  const when=(s:Segment)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:s.timezone}).format(new Date(Date.parse(s.recorded_at)+s.start_s*1000));
  const similar=(s:Segment)=>`${resolve(`/datasets/${id}/search`)}?recording=${s.recording_id}&start_s=${s.start_s}&end_s=${s.end_s}`;
  const pct=(share:number,digits=0)=>new Intl.NumberFormat(app.locale,{style:'percent',maximumFractionDigits:digits}).format(share);
  const groupName=(g:{node:number;name:string})=>g.name?fill('eGroup',{n:g.name}):t('eWhole');
  // Common names in the UI language are fetched once per species; the scientific name is shown until then or when there is none.
  const asked=new Set<string>();
  function askNames(items:Named[]){for(const {id:sid} of items)if(sid&&!asked.has(sid)){asked.add(sid);
    api<Species>(`/species/${encodeURIComponent(sid)}`).then(row=>{names[sid]=row;},()=>{/* The scientific name stays as the fallback. */});}}
  const speciesName=(s:Named)=>{const row=s.id?names[s.id]:undefined;return (row&&commonName(row,app.locale))||s.scientific_name;};
  const listOf=<T extends Named>(items:T[],share:(item:T)=>number)=>items.map(item=>`${speciesName(item)} ${pct(share(item))}`).join(t('eSep'));
  // Below one interval in five with any AI suggestion, the group is more likely silence or insects than a species.
  const aiHint=(g:Group)=>g.hint_rate<.2||!g.top_species.length?fill('eNoAiHint',{rate:pct(g.hint_rate)})
    :fill('eAiHint',{list:listOf(g.top_species,s=>s.share),rate:pct(g.hint_rate)});
  const candidates=(s:Segment)=>s.candidates.length?fill('eCandidates',{list:listOf(s.candidates,c=>c.score)}):t('eNoCandidates');
  const made=(at:string|null)=>at?new Intl.DateTimeFormat(app.locale,{dateStyle:'medium'}).format(new Date(`${at.replace(' ','T')}Z`)):'';

  // Scatter: every point is drawn once into an offscreen canvas; pan and zoom move that image and redraw sharply when the gesture pauses.
  let points:Points|null=null,canvas:HTMLCanvasElement|null=null,offscreen:HTMLCanvasElement|null=null,timer:ReturnType<typeof setTimeout>|undefined,animation=0;
  // Device pixels: screen = base * k + t, where base fits all points into the canvas.
  let view={k:1,tx:0,ty:0},drawn={k:1,tx:0,ty:0},drag:{x:number;y:number;tx:number;ty:number;moved:boolean}|null=null;
  // Where each child's numbered badge goes: the median of its points, in map coordinates.
  let marks:{x:number;y:number}[]=[];
  async function loadPoints(status:Status){
    pointsError='';
    try{const buffer=await api<ArrayBuffer>(`/explore/points?dataset=${id}`,undefined,'GET',true);
      if(status.created_at!==data?.created_at)return;
      // uint32 n, float32 x[n], float32 y[n], int64 segment_id[n]; little-endian, the native order of every supported PC. Positions are enough here.
      const n=new DataView(buffer).getUint32(0,true);
      if(buffer.byteLength!==4+16*n)throw Error('unknown_error');
      const x=new Float32Array(buffer,4,n),y=new Float32Array(buffer,4+4*n,n),box:Points['box']=[Infinity,-Infinity,Infinity,-Infinity];
      for(let i=0;i<n;i++){box[0]=Math.min(box[0],x[i]);box[1]=Math.max(box[1],x[i]);box[2]=Math.min(box[2],y[i]);box[3]=Math.max(box[3],y[i]);}
      points={n,x,y,box};fly(false);
    }catch(e){pointsError=e instanceof Error?e.message:'unknown_error';}
  }
  function frame(target:HTMLCanvasElement,{box:[x0,x1,y0,y1]}:Points,{k,tx,ty}=view){
    const ratio=target.width/Math.max(1,target.clientWidth),pad=8*ratio,{width,height}=target;
    return {ratio,x0,y0,sx:(width-2*pad)/(x1-x0||1)*k,sy:(height-2*pad)/(y1-y0||1)*k,ox:pad*k+tx,oy:(height-pad)*k+ty};
  }
  const median=(values:Float32Array)=>values.slice().sort()[values.length>>1];
  // Fit the view to the group being viewed; going into a group zooms there in 350 ms.
  function fly(animate:boolean){
    if(!points||!canvas||!node)return;
    const {x,y}=points,{width,height}=canvas,base=frame(canvas,points,{k:1,tx:0,ty:0});
    marks=children.map(c=>({x:median(x.subarray(c.lo,c.hi)),y:median(y.subarray(c.lo,c.hi))}));
    let left=Infinity,right=-Infinity,bottom=Infinity,top=-Infinity;
    for(let i=node.lo;i<node.hi;i++){left=Math.min(left,x[i]);right=Math.max(right,x[i]);bottom=Math.min(bottom,y[i]);top=Math.max(top,y[i]);}
    // The group's box in base pixels (k = 1, t = 0); canvas y grows downwards.
    [left,right]=[base.ox+(left-base.x0)*base.sx,base.ox+(right-base.x0)*base.sx];
    [top,bottom]=[base.oy-(top-base.y0)*base.sy,base.oy-(bottom-base.y0)*base.sy];
    const k=Math.min(MAX_ZOOM,Math.max(1,.85*Math.min(width/(right-left),height/(bottom-top))));
    const target={k,tx:width/2-k*(left+right)/2,ty:height/2-k*(top+bottom)/2};
    cancelAnimationFrame(animation);transformed=false;
    if(!animate||matchMedia('(prefers-reduced-motion: reduce)').matches){view=target;render();return;}
    // Zoom geometrically and move the centre linearly, as map apps do.
    const from=view,start=performance.now(),centre=(v:typeof view)=>[(width/2-v.tx)/v.k,(height/2-v.ty)/v.k];
    const [fx,fy]=centre(from),[gx,gy]=centre(target);
    const step=(now:number)=>{const u=Math.min(1,(now-start)/350),e=1-(1-u)**3,k=from.k*(target.k/from.k)**e;
      view={k,tx:width/2-k*(fx+(gx-fx)*e),ty:height/2-k*(fy+(gy-fy)*e)};render();
      if(u<1)animation=requestAnimationFrame(step);};
    animation=requestAnimationFrame(step);
  }
  // The group being viewed is coloured by its smaller groups; every other point stays faint for context.
  function render(){
    if(!points||!canvas||!offscreen||!node)return;
    const {n,x,y}=points,{ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),context=offscreen.getContext('2d')!;
    offscreen.width=canvas.width;offscreen.height=canvas.height;
    const dots=(lo:number,hi:number,size:number)=>{for(let i=lo;i<hi;i++)context.fillRect(ox+(x[i]-x0)*sx-size/2,oy-(y[i]-y0)*sy-size/2,size,size);};
    const paint=(index:number,alpha:number)=>{context.globalAlpha=alpha;context.fillStyle=palette[index%palette.length];dots(children[index].lo,children[index].hi,3*ratio);};
    context.globalAlpha=.1;context.fillStyle=getComputedStyle(canvas).color;dots(0,node.lo,2*ratio);dots(node.hi,n,2*ratio);
    if(!children.length){context.globalAlpha=.8;context.fillStyle=palette[0];dots(node.lo,node.hi,4*ratio);}
    children.forEach((_,index)=>{if(index!==selected)paint(index,selected===null?.8:.15);});
    if(selected!==null&&children[selected])paint(selected,.9);
    drawn={...view};blit();
  }
  function blit(){
    if(!canvas||!offscreen||!points)return;
    const context=canvas.getContext('2d')!,r=view.k/drawn.k;
    context.setTransform(1,0,0,1,0,0);context.clearRect(0,0,canvas.width,canvas.height);
    context.setTransform(r,0,0,r,view.tx-drawn.tx*r,view.ty-drawn.ty*r);context.drawImage(offscreen,0,0);
    // Numbered badges stay sharp and the same size at any zoom; the numbers match the list.
    const {ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points);
    context.setTransform(1,0,0,1,0,0);context.font=`700 ${12*ratio}px ${getComputedStyle(canvas).fontFamily}`;context.textAlign='center';context.textBaseline='middle';context.lineWidth=2*ratio;
    marks.forEach((mark,index)=>{const cx=ox+(mark.x-x0)*sx,cy=oy-(mark.y-y0)*sy;
      context.beginPath();context.arc(cx,cy,11*ratio,0,2*Math.PI);context.fillStyle=palette[index%palette.length];context.fill();
      context.strokeStyle='#fff';context.stroke();context.fillStyle='#fff';context.fillText(String(index+1),cx,cy);});
    // A ring marks the sound shown under the map.
    if(pickAt>=0&&pickAt<points.n){context.beginPath();context.arc(ox+(points.x[pickAt]-x0)*sx,oy-(points.y[pickAt]-y0)*sy,7*ratio,0,2*Math.PI);
      context.lineWidth=2.5*ratio;context.strokeStyle=getComputedStyle(canvas).color;context.stroke();}
  }
  function moved(){transformed=true;blit();clearTimeout(timer);timer=setTimeout(render,150);}
  $effect(()=>{void selected;void palette;untrack(render);});
  $effect(()=>{void pickAt;untrack(blit);});
  function scatter(element:HTMLCanvasElement){
    canvas=element;offscreen=document.createElement('canvas');
    const resize=new ResizeObserver(()=>{const r=devicePixelRatio||1;element.width=Math.round(element.clientWidth*r);element.height=Math.round(element.clientHeight*r);fly(false);});
    // Zoom around the cursor; a non-passive listener keeps the page from scrolling instead.
    const wheel=(event:WheelEvent)=>{event.preventDefault();
      const bounds=element.getBoundingClientRect(),r=element.width/bounds.width,cx=(event.clientX-bounds.left)*r,cy=(event.clientY-bounds.top)*r;
      const k=Math.min(MAX_ZOOM,Math.max(1,view.k*Math.exp(-event.deltaY*.002))),g=k/view.k;
      cancelAnimationFrame(animation);view={k,tx:cx-(cx-view.tx)*g,ty:cy-(cy-view.ty)*g};moved();};
    resize.observe(element);element.addEventListener('wheel',wheel,{passive:false});
    return {destroy(){resize.disconnect();element.removeEventListener('wheel',wheel);clearTimeout(timer);cancelAnimationFrame(animation);canvas=null;}};
  }
  function down(event:PointerEvent){if(event.button!==0)return;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId);drag={x:event.clientX,y:event.clientY,tx:view.tx,ty:view.ty,moved:false};}
  function move(event:PointerEvent){
    if(!drag||!canvas)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
    if(!drag.moved&&Math.hypot(dx,dy)<4)return;
    // Only a real drag stops a running zoom; a click right after going inside must not freeze it halfway.
    const r=canvas.width/Math.max(1,canvas.clientWidth);drag.moved=true;cancelAnimationFrame(animation);view={...view,tx:drag.tx+dx*r,ty:drag.ty+dy*r};moved();
  }
  // A click picks the nearest point of the group being viewed (within 8 px), shows and plays its sound and chooses its smaller group; a second click within 350 ms goes into it.
  function up(event:PointerEvent){
    const click=drag&&!drag.moved;drag=null;
    // Clicks right after going into a group are ignored, so a fast double click cannot go down two levels.
    if(!click||!points||!canvas||!node||performance.now()<quietUntil)return;
    const now=performance.now(),double=now-lastClick<350;lastClick=now;
    const {ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),bounds=canvas.getBoundingClientRect();
    const px=(event.clientX-bounds.left)*ratio,py=(event.clientY-bounds.top)*ratio;
    let best=-1,limit=(8*ratio)**2;
    for(let i=node.lo;i<node.hi;i++){const dx=ox+(points.x[i]-x0)*sx-px,dy=oy-(points.y[i]-y0)*sy-py,d=dx*dx+dy*dy;if(d<limit){limit=d;best=i;}}
    const child=children.findIndex(c=>c.lo<=best&&best<c.hi);
    if(double&&child>=0){void go(children[child].node);return;}
    // An empty spot clears the pick.
    if(best<0){selected=null;pickAt=-1;pick=null;return;}
    if(child>=0)reveal(child);
    if(best!==pickAt||pickError)void choose(best);
  }
</script>
{#snippet sounds(at:number)}
  {@const list=lists[at]}
  {#each list?.segments??[] as s (s.segment_id)}<div class="member">
    <p>{when(s)}<br><span class="muted">{candidates(s)}</span></p>
    <AudioRegion source={sourceOf(s)} fixedRange compact start={s.start_s} end={s.end_s}>
      {#snippet actions()}<Button size="sm" href={similar(s)}>{t('eFindSimilar')}</Button>{/snippet}
    </AudioRegion></div>{/each}
  {#if list?.error}<p class="error" role="alert">{t(list.error)}</p>{/if}
  {#if list?.segments.length&&list.segments.length<list.total}<Button pending={list.pending} onclick={()=>loadList(at,list.segments.length+PAGE)}>{t('eMore')}</Button>
  {:else if !list||list.pending}<p role="status"><Spinner/> {t('loading')}</p>{/if}
{/snippet}
<svelte:window onkeydown={escape}/>
<div class="page wide" onplaycapture={solo}>
  <div class="crumbs"><a href={resolve(`/datasets/${id}`)}>{t('uBackResults')}</a></div>
  <div class="page-header"><div><h1>{t('eTitle')}</h1><p>{t('eLead')}</p></div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if ended}<p class={ended.status==='failed'?'error':'notice'} role="status">{t(ended.status==='cancelled'?'cancelled':ended.error??'explore_failed')}</p>{/if}
  {#if data}
    {#if data.status==='building'||building}<section class="status-box" role="status">
      {#if building}<JobStatus job={building}/>{:else}<p><Spinner/> {t('eBuilding')}</p>{/if}
      <p class="muted">{t('eTakesMinutes')}</p></section>
    {:else if data.status==='stale'}<section class="notice stack" role="status">
      <p>{t('eStale')}</p>
      <div class="actions"><Button pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eRebuild')}</Button><span>{t(job&&!build.pending?'job_busy':'eTakesMinutes')}</span></div>
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}</section>
    {:else if !node}<section class="panel empty">
      <p>{t('eEmpty')}</p><p>{t('eTakesMinutes')}</p>
      <Button variant="primary" pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eBuild')}</Button>
      {#if job&&!build.pending}<p>{t('job_busy')}</p>{/if}
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}</section>{/if}
    {#if node}
      <p class="muted">{fill('eMadeFrom',{date:made(data.created_at),model:modelName(data.engine),n:data.segment_count.toLocaleString(app.locale)})}</p>
      <div class="explore">
        <section class="panel stack map-panel">
          <div class="section-heading"><h2>{t('eMapHeading')}</h2><Button size="sm" disabled={!transformed} onclick={()=>fly(false)}>{t('eReset')}</Button></div>
          {#if pointsError}<p class="error" role="alert">{t(pointsError)}</p>{/if}
          <!-- The list is the keyboard path to the same groups; the map adds pointer zoom, pan, picking and double-click to go inside. -->
          <canvas use:scatter aria-label={t('eMapLabel')} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={()=>drag=null}></canvas>
          <p class="muted">{fill('eMapHint',{s:+(node.rep.end_s-node.rep.start_s).toFixed(1)})}</p>
          <div class="pick">
            {#if pickAt<0}<p class="muted">{t('ePickHint')}</p>
            {:else if pickError}<p class="error" role="alert">{t(pickError)}</p>
            {:else if !pick}<p role="status"><Spinner/> {t('loading')}</p>
            {:else}
              {@const s=pick}
              {@const child=children.findIndex(c=>c.lo<=pickAt&&pickAt<c.hi)}
              <h3>{t('ePicked')} <span class="when">{when(s)}</span></h3>
              <p class="muted">{#if child>=0}{fill('ePickedGroup',{n:children[child].name})} · {/if}{candidates(s)}</p>
              <!-- A new pick mounts a new player; the click on the map is the gesture that lets it start on its own. -->
              <AudioRegion source={sourceOf(s)} fixedRange compact autoplay start={s.start_s} end={s.end_s}>
                {#snippet actions()}
                  {#if child>=0}<Button size="sm" onclick={()=>{open[children[child].node]=true;reveal(child,'start');}}>{t('eOpenGroup')}</Button>{/if}
                  <Button size="sm" href={similar(s)}>{t('eFindSimilar')}</Button>
                {/snippet}
              </AudioRegion>
            {/if}
          </div>
        </section>
        <section class="panel stack">
          <nav class="trail" aria-label={t('eTrail')}>
            {#each trail as step,index (step.node)}
              {#if index}<span class="muted" aria-hidden="true">›</span>{/if}
              {#if index<trail.length-1}<a href={here(step.node)}>{groupName(step)}</a>{:else}<strong aria-current="page">{groupName(step)}</strong>{/if}
            {/each}
          </nav>
          <div class="actions">
            <Button size="sm" href={parent===undefined?undefined:here(parent)} disabled={parent===undefined}>{t('eUp')}</Button>
            <Button size="sm" href={here(0)} disabled={!node.node}>{t('eTop')}</Button>
          </div>
          <p>{fill('eSummary',{n:node.size.toLocaleString(app.locale),share:pct(node.size/Math.max(1,data.segment_count),1)})}<br><span class="muted">{aiHint(node)}</span></p>
          {#if children.length}
            <h2>{t('eListHeading')}</h2>
            <p class="muted">{t('eListHint')}</p>
            <p class="muted">{t('localTime')}</p>
            {#each children as c,index (c.node)}
              <details class="group" class:selected={selected===index} class:dim={selected!==null&&selected!==index} id={`group-${c.node}`}
                bind:open={open[c.node]} ontoggle={event=>{if(event.currentTarget.open)void loadList(c.node);}}>
                <!-- Opening a row chooses its group on the map; closing the chosen row clears the choice. -->
                <summary onclick={()=>selected=open[c.node]?(selected===index?null:selected):index}>
                  <span class="badge" style:background={palette[index%palette.length]} aria-hidden="true">{index+1}</span>
                  <span class="head"><h3>{groupName(c)} — <span class="muted">{fill('eSize',{n:c.size.toLocaleString(app.locale),share:pct(c.size/node.size)})}</span></h3>
                    <span class="muted">{aiHint(c)}</span></span>
                  <span class="chevron" aria-hidden="true">›</span>
                </summary>
                {#if open[c.node]}<div class="body stack">
                  {#if c.child_count}<div class="actions"><Button size="sm" href={here(c.node)}>{t('eOpen')}</Button><span class="muted">{fill('eInner',{n:c.child_count})}</span></div>{/if}
                  {@render sounds(c.node)}
                </div>{/if}
              </details>
            {/each}
          {:else}
            <h2>{fill('eMembersHeading',{n:node.size.toLocaleString(app.locale)})}</h2>
            <p class="muted">{t('localTime')}</p>
            {@render sounds(node.node)}
          {/if}
        </section>
      </div>
    {/if}
  {:else if !error}<p role="status"><Spinner/> {t('eLoading')}</p>{/if}
</div>
<style>.explore{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
  .map-panel{position:sticky;top:16px}
  /* The map shrinks in short windows so the chosen sound under it stays in view. */
  canvas{display:block;width:100%;height:clamp(240px,calc(100vh - 560px),420px);border:1px solid rgb(var(--color-card-border));border-radius:8px;background:rgb(var(--stone-50));touch-action:none;cursor:pointer}
  .pick{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .pick .when{font-size:16px;font-weight:400}
  .trail{display:flex;flex-wrap:wrap;align-items:center;gap:0 4px}
  .trail a,.trail strong{display:inline-flex;align-items:center;min-height:44px;padding:0 4px}.trail strong{font-weight:600}
  .group{font-size:16px;border:1px solid rgb(var(--color-card-border));border-radius:8px;transition:opacity .15s}
  .group.selected{border-left:4px solid rgb(var(--primary-700))}.group.dim{opacity:.55}
  .group>summary{display:grid;grid-template-columns:28px minmax(0,1fr) 16px;gap:8px;align-items:start;padding:12px;list-style:none;font-weight:400}
  .group>summary::-webkit-details-marker{display:none}
  .head{display:flex;flex-direction:column;gap:4px}.head .muted{font-weight:400}
  .chevron{color:rgb(var(--stone-600));font-size:18px;line-height:28px;text-align:center;transition:transform .15s}
  .group[open] .chevron{transform:rotate(90deg)}
  .body{padding:12px;border-top:1px solid rgb(var(--color-card-border))}
  .badge{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;color:#fff;font-size:14px;font-weight:700}
  .member{display:flex;flex-direction:column;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .empty :global(.button){align-self:center}
  @media(max-width:900px){.explore{grid-template-columns:minmax(0,1fr)}.map-panel{position:static}canvas{height:320px}}</style>
