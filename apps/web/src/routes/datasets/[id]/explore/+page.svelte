<script lang="ts" module>
  // Back and forward walk the groups viewed on each map. The page remounts on every group change, so the list lives here until the app closes.
  const visits=new Map<number,{created_at:string;stack:number[];index:number}>();
</script>
<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,datasetOf,refreshCatalog,siteOf } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { fill,modelName } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import PickForm,{blanks,pickedSounds,pickKey,recentSpecies,type Named} from '$lib/PickForm.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Segment={segment_id:number;recording_id:number;path:string;start_s:number;end_s:number;recorded_at:string;timezone:string;candidates:(Named&{score:number})[]};
  // Points come in tree order, so the sounds of every group are the positions lo..hi-1 of the point arrays.
  type Group={node:number;name:string;size:number;lo:number;hi:number;child_count:number;rep:{start_s:number;end_s:number};hint_rate:number;top_species:(Named&{share:number})[]};
  type Status={status:'none'|'building'|'ready'|'stale';stale_reason?:'data'|'method';created_at:string|null;engine:string;segment_count:number;last_error?:string;node?:Group;path?:{node:number;name:string}[];children?:Group[]};
  type Page={total:number;segments:Segment[]};
  // `anchor`: the position the sounds are sorted around, or -1 for the group's centre.
  type List=Page&{pending:boolean;error:string;anchor:number};
  type Points={n:number;x:Float32Array;y:Float32Array;box:[number,number,number,number]};
  const id=Number(page.params.id),PAGE=20,MAX_ZOOM=1000,draftKey=`echoroo-explore:${id}`;
  const site=$derived(siteOf(datasetOf(id)));
  // Group species were dropped; clear what older versions kept in this browser.
  try{localStorage.removeItem(`echoroo-explore-species:${id}`);}catch{/* Private windows may refuse storage. */}
  // Categorical hues stepped for the light and the dark surface; a group has at most eight smaller groups, one colour each.
  const LIGHT=['#2a78d6','#eb6834','#1baf7a','#eda100','#e87ba4','#008300','#6250d6','#e34948'];
  const DARK=['#3987e5','#d95926','#199e70','#c98500','#d55181','#008300','#9085e9','#e66767'];
  const palette=$derived(app.dark?DARK:LIGHT);
  // The group being viewed lives in the URL (`?node=`). Moving between groups replaces it, so the browser's back button leaves the map; back and forward on the map walk the groups.
  const nodeId=$derived(Number(new URL(page.url.hash.slice(1),page.url).searchParams.get('node'))||0);
  const here=(node:number)=>`${resolve(`/datasets/${id}/explore`)}?node=${node}`;
  // Coming back without `?node=` reopens the same group, rows, current sound and as many sounds as before.
  let draft=(()=>{try{return JSON.parse(localStorage.getItem(draftKey)||'null');}catch{return null;}})() as {created_at?:string;node?:number;loaded?:Record<string,number>;pick?:number}|null;
  let restoring=!new URL(page.url.hash.slice(1),page.url).searchParams.has('node')&&!!draft;
  let data=$state<Status|null>(null),error=$state(''),pointsError=$state(''),names=$state<Record<string,Species>>({});
  // The child picked on the map or opened in the list (its index): its row is highlighted and the other children are dimmed.
  let selected=$state<number|null>(null);
  // Open rows and the loaded sounds of each group, by group number; the sounds stay cached while the same map is shown.
  let open=$state<Record<number,boolean>>({}),lists=$state<Record<number,List>>({});
  // The sound picked on the map: its position in the point arrays, then its details and the position they belong to.
  // The last sound stays shown until the next one arrives, so the card never shrinks and the page never jumps.
  let pickAt=$state(-1),pick=$state<Segment|null>(null),shownAt=$state(-1),pickError=$state(''),autoplayPick=$state(true);
  let watched=$state<number|null>(null),transformed=$state(false),canBack=$state(false),canForward=$state(false);
  // Sounds in a group are listed nearest to the current sound; until a dot is clicked, nearest to the group's centre.
  const anchorOf=()=>pickAt;
  const node=$derived(data?.node),children=$derived(data?.children??[]),trail=$derived(data?.path??[]),parent=$derived(trail.at(-2)?.node);
  // Sounds picked for export, saved in the sidecar; the line under the map counts them and what is still blank.
  const picks=pickedSounds(id);void picks.reload();
  const tally=$derived.by(()=>{const items=Object.values(picks.state.items);return {n:items.length,blanks:blanks(items)};});
  const recent=$derived(recentSpecies(Object.values(picks.state.items)));
  const job=$derived(activeJob());
  const building=$derived(job?.stage.startsWith('explore_')&&datasetOf(job.dataset_id)?.site_id===datasetOf(id)?.site_id?job:null);
  const ended=$derived(app.jobs.find(j=>j.id===watched&&(j.status==='failed'||j.status==='cancelled')));
  // Why a build stopped; a full drive is reported as `explore_disk_space:<needed bytes>:<free bytes>`.
  const why=(code:string)=>{const [key,need,free]=code.split(':'),size=(bytes:string)=>new Intl.NumberFormat(app.locale,{style:'unit',unit:'gigabyte',maximumFractionDigits:1}).format(Number(bytes)/1e9);
    return key==='explore_disk_space'?fill(key,{need:size(need),free:size(free)}):t(code);};
  const signature=$derived(app.jobs.map(j=>`${j.id}:${j.status}`).join(','));
  $effect(()=>{if(building)watched=building.id;});
  // The catalogue poll refreshes jobs every 2 s; any job change or a move to another group reloads the map state.
  $effect(()=>{void signature;void nodeId;untrack(()=>void load());});

  let request=0,quietUntil=0,lastClick=0;
  async function load(){
    const version=++request,wanted=nodeId;
    try{const next=await api<Status>(`/explore?dataset=${id}&node=${wanted}`);
      if(version!==request)return;
      if(restoring){restoring=false;if(next.created_at===draft?.created_at&&draft?.node&&draft.node!==wanted){void go(draft.node);return;}}
      const before=data?.node,fresh=next.created_at!==data?.created_at,moved=next.node?.node!==before?.node;
      data=next;error='';
      if(fresh){points=null;lists={};if(next.node)void loadPoints(next);}
      if(!next.node)return;
      // Every group shown joins the back/forward list, unless back or forward brought us here.
      let visited=visits.get(id);
      if(visited?.created_at!==next.created_at){visited={created_at:next.created_at!,stack:[],index:-1};visits.set(id,visited);}
      if(visited.stack[visited.index]!==next.node.node){visited.stack=[...visited.stack.slice(0,visited.index+1),next.node.node];visited.index=visited.stack.length-1;}
      canBack=visited.index>0;canForward=visited.index<visited.stack.length-1;
      if(fresh||moved){
        selected=null;open={};pickAt=-1;pick=null;quietUntil=performance.now()+400;
        const kept=draft?.created_at===next.created_at?draft:null,saved=kept?.node===next.node.node?kept.loaded??{}:{};draft=null;
        // The sound chosen before going inside (or before leaving the page) stays chosen when it is in this group, without playing again.
        const was=kept?.pick??-1;
        if(was>=next.node.lo&&was<next.node.hi){const at=(next.children??[]).findIndex(c=>c.lo<=was&&was<c.hi);selected=at<0?null:at;void choose(was,false);
          // Going inside with a chosen sound opens the smaller group it belongs to, as a click would.
          if(at>=0&&!Object.keys(saved).length){open[next.children![at].node]=true;bringUp(next.children![at].node);}}
        // A group that is not divided further shows its own sounds instead of rows.
        if(!next.children?.length)void loadList(next.node.node,saved[next.node.node]);
        for(const c of next.children??[])if(saved[c.node]){open[c.node]=true;void loadList(c.node,saved[c.node]);}
        fly(!!before);
      }
      askNames([next.node,...next.children??[]].flatMap(g=>g.top_species));
    }catch(e){if(version!==request)return;const code=e instanceof Error?e.message:'unknown_error';
      // A group of an older map no longer exists: start again from the whole map.
      if(code==='not_found'&&wanted){void go(0);return;}
      error=code;}
  }
  function go(target:number){clearTimeout(scrollTimer);quietUntil=performance.now()+400;return goto(here(target),{replaceState:true,noScroll:true});}
  function step(by:number){const visited=visits.get(id),target=visited?.stack[visited.index+by];if(visited&&target!==undefined){visited.index+=by;void go(target);}}
  // At the top already, the button only brings the whole map back into view.
  function whole(){if(node?.node)void go(0);else fly(true);}
  // Group numbers change when the map is made again, so a new map starts from the whole map.
  const build=createTask(async()=>{watched=(await api<{id:number}>('/explore',{dataset_id:id})).id;await refreshCatalog();if(nodeId)void go(0);});
  // Loads a group's sounds in order until `count` are loaded or none are left.
  async function loadList(at:number,count=PAGE){
    const anchor=anchorOf();
    lists[at]??={total:0,segments:[],pending:false,error:'',anchor};
    const list=lists[at],sorted=list.anchor!==anchor;
    if(list.pending||!sorted&&list.segments.length>=Math.max(1,Math.min(count,list.total)))return;
    list.pending=true;list.error='';
    // A new order loads aside and replaces the shown sounds at once, so the rows never shrink while loading.
    const segments=sorted?[]:[...list.segments];let total=list.total;
    try{do{const next=await api<Page>(`/explore/nodes/${at}/segments?dataset=${id}&offset=${segments.length}${anchor>=0?`&near=${anchor}`:''}`);
        total=next.total;segments.push(...next.segments);askNames(next.segments.flatMap(s=>s.candidates));
        if(!next.segments.length)break;
      }while(segments.length<Math.min(Math.max(count,PAGE),total));
      list.total=total;list.segments=segments;list.anchor=anchor;
    }catch(e){list.error=e instanceof Error?e.message:'unknown_error';}
    finally{list.pending=false;}
    // Another dot may have been clicked while this was loading.
    if(!list.error&&list.anchor!==anchorOf())void loadList(at,list.segments.length);
  }
  // A new current sound re-sorts the shown lists; each keeps as many sounds as it shows.
  $effect(()=>{const anchor=anchorOf();untrack(()=>{for(const [at,list] of Object.entries(lists))
    if(list.anchor!==anchor&&(open[+at]||+at===node?.node))void loadList(+at,list.segments.length);});});
  $effect(()=>{if(!node||!data?.created_at)return;
    const shown=children.length?children.filter(c=>open[c.node]).map(c=>c.node):[node.node];
    const value=JSON.stringify({created_at:data.created_at,node:node.node,loaded:Object.fromEntries(shown.map(at=>[at,lists[at]?.segments.length||PAGE])),pick:pickAt});
    try{localStorage.setItem(draftKey,value);}catch{/* Private windows may refuse storage. */}});
  async function choose(at:number,play=true){
    pickAt=at;pickError='';autoplayPick=play;
    try{const next=await api<Segment>(`/explore/points/${at}?dataset=${id}`);
      if(pickAt===at){pick=next;shownAt=at;askNames(next.candidates);}
    }catch(e){if(pickAt===at)pickError=e instanceof Error?e.message:'unknown_error';}
  }
  // Ticking a sound adds it for export with nothing filled in; unticking removes it and its labels.
  function pickSound(s:Segment,checked:boolean){
    if(!checked){void picks.remove(s);return;}
    void picks.save({recording_id:s.recording_id,start_s:s.start_s,end_s:s.end_s,path:s.path,recorded_at:s.recorded_at,timezone:s.timezone,
      species:null,background:[],sound_type:'',quality:'',others:false,exported_at:null,updated_at:''});
  }
  // Picking a sound opens its group's row; rows share one `name`, so the browser closes the row that was open.
  // A newly opened row out of view is brought up after the double-click window, so a fast second click still lands on the same dot.
  let scrollTimer:ReturnType<typeof setTimeout>|undefined;
  function bringUp(at:number){clearTimeout(scrollTimer);
    scrollTimer=setTimeout(()=>{const row=document.getElementById(`group-${at}`),main=row?.closest('main');if(!row||!main)return;
      const top=row.getBoundingClientRect().top-main.getBoundingClientRect().top;
      if(top<0||top>main.clientHeight*.6)row.scrollIntoView({block:'start',behavior:'smooth'});},360);}
  function show(child:number){selected=child;const at=children[child].node;if(!open[at]){open[at]=true;bringUp(at);}}
  function escape(event:KeyboardEvent){if(event.key==='Escape'&&!event.defaultPrevented&&parent!==undefined)void go(parent);}
  // One sound at a time: starting a player pauses every other one on the page.
  function solo(event:Event){for(const audio of (event.currentTarget as HTMLElement).querySelectorAll('audio'))if(audio!==event.target)audio.pause();}
  // A fixed range only plays start–end, so the clip end stands in for the recording length.
  const sourceOf=(s:Segment)=>({id:s.recording_id,kind:'recording' as const,path:s.path,seconds:s.end_s});
  const when=(s:Segment)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',timeZone:s.timezone}).format(new Date(Date.parse(s.recorded_at)+s.start_s*1000));
  const pct=(share:number,digits=0)=>new Intl.NumberFormat(app.locale,{style:'percent',maximumFractionDigits:digits}).format(share);
  const groupName=(g:{node:number;name:string})=>g.name?fill('eGroup',{n:g.name}):t('eWhole');
  // Common names in the UI language are fetched once per species; the scientific name is shown until then or when there is none.
  const asked=new Set<string>();
  function askNames(items:Named[]){for(const {id:sid} of items)if(sid&&!asked.has(sid)){asked.add(sid);
    api<Species>(`/species/${encodeURIComponent(sid)}`).then(row=>{names[sid]=row;},()=>{/* The scientific name stays as the fallback. */});}}
  const speciesName=(s:Named)=>{const row=s.id?names[s.id]:undefined;return (row&&commonName(row,app.locale))||s.scientific_name;};
  $effect(()=>{askNames(Object.values(picks.state.items).flatMap(i=>[...i.species?[i.species]:[],...i.background]));});
  const listOf=<T extends Named>(items:T[],share:(item:T)=>number)=>items.map(item=>`${speciesName(item)} ${pct(share(item))}`).join(t('eSep'));
  // Below one interval in five with any AI suggestion, the group is more likely silence or insects than a species.
  const suggested=(g:Group)=>g.hint_rate<.2?[]:g.top_species;
  const aiHint=(g:Group)=>!suggested(g).length?fill('eNoAiHint',{rate:pct(g.hint_rate)})
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
  // A click picks the nearest point of the group being viewed (within 8 px), shows and plays its sound and highlights its smaller group without scrolling; a second click within 350 ms goes into it.
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
    if(best!==pickAt||pickError)void choose(best);
    if(child>=0)show(child);
  }
</script>
<!-- In the list the line wraps, and the row of the current sound is marked. -->
{#snippet about(s:Segment,current=false)}<span class="about" title={`${when(s)} · ${candidates(s)}`}>{#if current}<span class="current">{t('eCurrentMark')}</span>{/if}{when(s)} · <span class="muted">{candidates(s)}</span></span>{/snippet}
{#snippet sounds(at:number)}
  {@const list=lists[at]}
  {#each list?.segments??[] as s (s.segment_id)}{@const p=picks.state.items[pickKey(s)]}<div class="member" class:picked={!!p}>
    <label class="check"><input type="checkbox" checked={!!p} onchange={event=>pickSound(s,event.currentTarget.checked)}/>{t('ePick')}</label>
    <AudioRegion source={sourceOf(s)} fixedRange compact dense start={s.start_s} end={s.end_s}>{#snippet meta()}{@render about(s,pick?.segment_id===s.segment_id)}{/snippet}</AudioRegion>
    {#if p}<div class="labels"><PickForm value={p} {recent} name={speciesName} onchange={label=>picks.save({...p,...label})}/></div>{/if}
  </div>{/each}
  {#if list?.error}<p class="error" role="alert">{t(list.error)}</p>{/if}
  {#if list?.segments.length&&list.segments.length<list.total}<Button pending={list.pending} onclick={()=>loadList(at,list.segments.length+PAGE)}>{t('eMore')}</Button>
  {:else if !list||list.pending}<p role="status"><Spinner/> {t('loading')}</p>{/if}
{/snippet}
{#snippet orderNote()}<p class="muted">{t(pickAt<0?'eOrderNeedsPick':'eOrderNear')}<br>{t('localTime')}</p>{/snippet}
<svelte:window onkeydown={escape}/>
<div class="page wide" onplaycapture={solo}>
  <div class="crumbs"><a href={resolve(`/datasets/${id}`)}>{t('uBackResults')}</a></div>
  <!-- The three steps of the page; the list numbers them, so the drawn numbers are hidden from screen readers. -->
  <div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{t('eTitle')}</h1>
    <ol class="flow">{#each [1,2,3] as n (n)}<li><span class="num" aria-hidden="true">{n}</span><strong>{t(`eStep${n}`)}</strong><p class="muted">{t(`eStep${n}Text`)}</p></li>{/each}</ol></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if ended}<p class={ended.status==='failed'?'error':'notice'} role="status">{ended.status==='cancelled'?t('cancelled'):why(ended.error??'explore_failed')}</p>
  {:else if data?.last_error&&!building}<p class="error" role="alert">{why(data.last_error)}</p>{/if}
  {#if data}
    {#if data.status==='building'||building}<section class="status-box" role="status">
      {#if building}<JobStatus job={building}/>{:else}<p><Spinner/> {t('eBuilding')}</p>{/if}
      <p class="muted">{t('eTakesTime')}</p></section>
    {:else if data.status==='stale'}<section class="notice stack" role="status">
      <p>{t(data.stale_reason==='method'?'eStaleMethod':'eStale')}</p>
      <div class="actions"><Button pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eRebuild')}</Button><span>{t(job&&!build.pending?'job_busy':'eTakesTime')}</span></div>
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}</section>
    {:else if !node}<section class="panel empty">
      <p>{t('eEmpty')}</p><p>{t('eTakesTime')}</p>
      <Button variant="primary" pending={build.pending} disabled={!!job} onclick={()=>build.run()}>{t('eBuild')}</Button>
      {#if job&&!build.pending}<p>{t('job_busy')}</p>{/if}
      {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
      {#if build.error==='no_search_embeddings'}<Button href={resolve(`/datasets/${id}`)}>{t('eBackToAnalyse')}</Button>{/if}</section>{/if}
    {#if node}
      <p class="muted">{fill('eMadeFrom',{date:made(data.created_at),model:modelName(data.engine),n:data.segment_count.toLocaleString(app.locale)})}</p>
      <div class="explore">
        <section class="panel stack map-panel" aria-label={t('eMapHeading')}>
          {#if pointsError}<p class="error" role="alert">{t(pointsError)}</p>{/if}
          <!-- The list is the keyboard path to the same groups; the map adds pointer zoom, pan, picking and double-click to go inside. -->
          <div class="map">
            <canvas use:scatter aria-label={t('eMapLabel')} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={()=>drag=null}></canvas>
            <div class="map-nav" role="group" aria-label={t('eMapNav')}>
              <Button size="sm" disabled={!canBack} onclick={()=>step(-1)}>{t('eBackView')}</Button>
              <Button size="sm" disabled={!canForward} onclick={()=>step(1)}>{t('eForwardView')}</Button>
              <Button size="sm" disabled={!node.node&&!transformed} onclick={whole}>{t('eTop')}</Button>
            </div>
          </div>
          <p class="muted">{fill('eMapHint',{s:+(node.rep.end_s-node.rep.start_s).toFixed(1)})}</p>
          <!-- Fixed text lines and a kept-up card: a new pick changes what the card shows, never how tall it is. -->
          <div class="pick" aria-busy={pickAt>=0&&!pickError&&shownAt!==pickAt}>
            {#if pickError}<p class="error" role="alert">{t(pickError)}</p>
            {:else if !pick}{#if pickAt<0}<p class="muted">{t('ePickHint')}</p>{:else}<p role="status"><Spinner/> {t('loading')}</p>{/if}
            {:else}
              {@const s=pick}
              {@const child=children.findIndex(c=>c.lo<=shownAt&&shownAt<c.hi)}
              <h3 class="line">{t('ePicked')}{#if child>=0}{' · '}{groupName(children[child])}{/if}{#if shownAt!==pickAt} <Spinner/>{/if}</h3>
              <!-- A new pick mounts a new player; the click on the map is the gesture that lets it start on its own. -->
              {#key s.segment_id}<AudioRegion source={sourceOf(s)} fixedRange compact dense autoplay={autoplayPick} start={s.start_s} end={s.end_s}>{#snippet meta()}{@render about(s)}{/snippet}</AudioRegion>{/key}
            {/if}
          </div>
          <!-- One row of fixed height, empty until a sound is ticked: ticking or labelling a sound changes the numbers, never the map's size. -->
          <div class="tally">
            {#if tally.n}<p><strong>{fill('eTally',{n:tally.n})}</strong>
                {#if tally.blanks}<span title={tally.blanks}>{tally.blanks}</span>{/if}</p>
              <Button variant="primary" size="sm" href={resolve(`/datasets/${id}/explore/picked`)}>{t('eToPicked')}</Button>{/if}
          </div>
          {#if picks.state.error}<p class="error" role="alert">{t(picks.state.error)}</p>{/if}
        </section>
        <section class="panel stack">
          <nav class="trail" aria-label={t('eTrail')}>
            {#each trail as step,index (step.node)}
              {#if index}<span class="muted" aria-hidden="true">›</span>{/if}
              {#if index<trail.length-1}<a href={here(step.node)} data-sveltekit-replacestate data-sveltekit-noscroll>{groupName(step)}</a>{:else}<strong aria-current="page">{groupName(step)}</strong>{/if}
            {/each}
          </nav>
          <p>{fill('eSummary',{n:node.size.toLocaleString(app.locale),share:pct(node.size/Math.max(1,data.segment_count),1)})}<br><span class="muted">{aiHint(node)}</span></p>
          {#if children.length}
            <h2>{t('eListHeading')}</h2>
            <p class="muted">{t('eListHint')}</p>
            {@render orderNote()}
            {#each children as c,index (c.node)}
              <details class="group" name="groups" class:selected={selected===index} class:dim={selected!==null&&selected!==index} id={`group-${c.node}`}
                bind:open={open[c.node]} ontoggle={event=>{if(event.currentTarget.open)void loadList(c.node);}}>
                <!-- Opening a row chooses its group on the map; closing the chosen row clears the choice. -->
                <summary onclick={()=>selected=open[c.node]?(selected===index?null:selected):index}>
                  <span class="badge" style:background={palette[index%palette.length]} aria-hidden="true">{index+1}</span>
                  <span class="head"><h3>{groupName(c)} — <span class="muted">{fill('eSize',{n:c.size.toLocaleString(app.locale),share:pct(c.size/node.size)})}</span></h3>
                    <span class="muted">{aiHint(c)}</span></span>
                  <span class="chevron" aria-hidden="true">›</span>
                </summary>
                {#if open[c.node]}<div class="body stack">
                  {#if c.child_count}<div class="actions"><Button size="sm" onclick={()=>go(c.node)}>{t('eOpen')}</Button><span class="muted">{fill('eInner',{n:c.child_count})}</span></div>{/if}
                  {@render sounds(c.node)}
                </div>{/if}
              </details>
            {/each}
          {:else}
            <h2>{fill('eMembersHeading',{n:node.size.toLocaleString(app.locale)})}</h2>
            {@render orderNote()}
            {@render sounds(node.node)}
          {/if}
        </section>
      </div>
    {/if}
  {:else if !error}<p role="status"><Spinner/> {t('eLoading')}</p>{/if}
</div>
<style>.flow{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;margin:12px 0 0;padding:0;list-style:none}
  .flow li{display:grid;grid-template-columns:28px minmax(0,1fr);gap:4px 8px;align-items:center;padding:12px;border:1px solid rgb(var(--color-card-border));border-radius:8px;background:rgb(var(--color-card-bg))}
  .flow .num{display:grid;place-items:center;width:28px;height:28px;border-radius:50%;background:rgb(var(--primary-700));color:#fffaf3;font-size:14px;font-weight:600}
  :global(.dark) .flow .num{background:rgb(var(--primary-500));color:rgb(var(--stone-50))}
  .flow p{grid-column:2;margin:0}
  .explore{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px;align-items:start}
  /* The map column stays in view and never runs past the window: 140px is the 76px top bar, the 32px page padding and 16px above and below. */
  .map-panel{position:sticky;top:16px;max-height:calc(100vh - 140px);overflow-y:auto}
  /* The map shrinks in short windows so the current sound under it stays in view. */
  .map{position:relative}.map-nav{position:absolute;top:8px;left:8px;display:flex;gap:4px}
  /* The map takes the height left over by the hint (three lines at 1024px), the current-sound card and the 44px tally row with their 12px gaps, so the column fits a 768px window. */
  canvas{display:block;width:100%;height:clamp(220px,calc(100vh - 540px),560px);border:1px solid rgb(var(--color-card-border));border-radius:8px;background:rgb(var(--stone-50));touch-action:none;cursor:pointer}
  /* As tall as a shown sound, so showing, changing or clearing one never changes the page height. */
  .pick{display:flex;flex-direction:column;gap:8px;min-height:198px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .line{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .trail{display:flex;flex-wrap:wrap;align-items:center;gap:0 4px}
  .trail a,.trail strong{display:inline-flex;align-items:center;min-height:44px;padding:0 4px}.trail strong{font-weight:600}
  .group{scroll-margin-top:16px;font-size:16px;border:1px solid rgb(var(--color-card-border));border-radius:8px;transition:opacity .15s}
  .group.selected{border-left:4px solid rgb(var(--primary-700))}.group.dim{opacity:.55}
  .group>summary{display:grid;grid-template-columns:28px minmax(0,1fr) 16px;gap:8px;align-items:start;padding:12px;list-style:none;font-weight:400}
  .group>summary::-webkit-details-marker{display:none}
  .head{display:flex;flex-direction:column;gap:4px}.head .muted{font-weight:400}
  .chevron{color:rgb(var(--stone-600));font-size:18px;line-height:28px;text-align:center;transition:transform .15s}
  .group[open] .chevron{transform:rotate(90deg)}
  .body{padding:12px;border-top:1px solid rgb(var(--color-card-border))}
  .badge{display:inline-grid;place-items:center;width:28px;height:28px;border-radius:50%;color:#fff;font-size:14px;font-weight:700}
  /* A 56px first column holds the check, its label under the box, left of the play button; a ticked sound's labels sit under it. */
  .member{display:grid;grid-template-columns:56px minmax(0,1fr);gap:8px;align-items:center;padding-top:8px;border-top:1px solid rgb(var(--stone-300))}
  .member.picked{margin:0 -12px;padding:8px 12px 12px;background:rgb(var(--primary-500)/.12)}
  /* The labels use the whole row width, from under the check, so their items stay on one line in a narrow column. */
  .labels{grid-column:1/-1}
  /* English labels wrap at the space; Japanese stays one word. */
  .check{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:44px;font-size:14px;font-weight:400;line-height:1.2;text-align:center;word-break:keep-all;cursor:pointer}
  .check input{width:22px;height:22px;accent-color:rgb(var(--primary-700))}
  /* The date and AI suggestions of a listed sound wrap instead of being cut: English needs three lines or more for two or three suggestions. */
  .member .about{white-space:normal}
  /* Outlined, so the mark stays visible on a ticked row's tint; the word, not the colour, carries the meaning. */
  .current{display:inline-block;margin-right:6px;padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;background:rgb(var(--color-card-bg));color:rgb(var(--primary-700));font-size:13px;font-weight:600;line-height:1.5}
  .tally{display:flex;align-items:center;justify-content:space-between;gap:12px;min-height:44px}
  .tally p{min-width:0;font-size:14px;line-height:1.3}
  .tally p>*{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tally :global(.button){flex:none}
  .empty :global(.button){align-self:center}
  @media(max-width:700px){.flow{grid-template-columns:minmax(0,1fr)}}
  @media(max-width:900px){.explore{grid-template-columns:minmax(0,1fr)}.map-panel{position:static;max-height:none;overflow:visible}canvas{height:320px}}</style>
