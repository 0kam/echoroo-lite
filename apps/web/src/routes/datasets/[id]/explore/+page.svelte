<script lang="ts" module>
  // Back and forward walk the groups viewed on each map. The page remounts on every group change, so the list lives here until the app closes.
  const visits=new Map<number,{created_at:string;stack:number[];index:number}>();
  // A sound clicked outside the group being viewed: the page made for its group shows and plays it.
  let carry=-1;
</script>
<script lang="ts">
  import { resolve } from '$app/paths';
  import { goto } from '$app/navigation';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,activeJob,datasetOf,refreshCatalog,siteOf } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { fill,modelIntro } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
  import MapEntries,{HEARD,NOT_HEARD,type MapOverlay} from '$lib/MapEntries.svelte';
  import SpeciesChoice from '$lib/SpeciesChoice.svelte';
  import {recordText,taxonName,type HeardRecord,type Taxon,type Verdict} from '$lib/heard';
  import PickForm,{blanks,pickedSounds,pickKey,recentSpecies,type Named} from '$lib/PickForm.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  type Segment={position?:number;segment_id:number;recording_id:number;path:string;start_s:number;end_s:number;recorded_at:string;timezone:string;candidates:(Named&{score:number})[]};
  // Points come in tree order, so the sounds of every group are the positions lo..hi-1 of the point arrays.
  type Group={node:number;name:string;size:number;lo:number;hi:number;child_count:number;rep:{start_s:number;end_s:number};hint_rate:number;top_species:(Named&{share:number})[]};
  type Status={status:'none'|'building'|'ready'|'stale';stale_reason?:'data'|'method';created_at:string|null;engine:string;segment_count:number;last_error?:string;node?:Group;path?:{node:number;name:string}[];children?:Group[]};
  type Page={total:number;segments:Segment[]};
  // `anchor`: the order the sounds were loaded in (see `anchorOf`).
  type List=Page&{pending:boolean;error:string;anchor:string};
  type Points={n:number;x:Float32Array;y:Float32Array;box:[number,number,number,number]};
  const id=Number(page.params.id),PAGE=20,MAX_ZOOM=10000,draftKey=`echoroo-explore:${id}`;
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
  // The sounds the open way points at come first; `order` counts its changes, so the shown lists are sorted again.
  let order=$state(0);
  const anchorOf=()=>`${pickAt}:${order}`;
  const node=$derived(data?.node),children=$derived(data?.children??[]),trail=$derived(data?.path??[]),parent=$derived(trail.at(-2)?.node);
  // Sounds collected to save as a ZIP, kept in the sidecar; the last section of the page counts them and what is still blank.
  const picks=pickedSounds(id);void picks.reload();
  // The answer card under the map: what is recorded on the shown sound, and an answer about the species chosen in "where to look".
  let entries=$state<ReturnType<typeof MapEntries>|null>(null),wanted=$state<Taxon|null>(null),recorded=$state<HeardRecord[]>([]),answering=$state(false),choosing=$state(false),answerError=$state('');
  // Every answer given by ear on the sounds of the group being viewed, newest first, for the short list beside the map.
  let inGroup=$state<(HeardRecord&{position:number})[]>([]);
  const loadRecorded=()=>{const at=node?.node;if(at!==undefined)api<typeof inGroup>(`/explore/recorded?dataset=${id}&node=${at}`).then(value=>{if(node?.node===at)inGroup=value;},()=>{/* The list keeps what it showed. */});};
  const at=(s:Segment)=>`recording_id=${s.recording_id}&start_s=${s.start_s}&end_s=${s.end_s}`;
  $effect(()=>{const s=pick;void picks.state.items;untrack(()=>{choosing=false;answerError='';if(!s){recorded=[];return;}
    api<{now:HeardRecord[]}>(`/datasets/${id}/heard/sound?${at(s)}&blank=true`).then(value=>{if(pick===s)recorded=value.now;},()=>{/* The card still takes an answer. */});});});
  // `species` null: "no bird sound". The mark on the map and the list beside it change at once; the numbers of the open way follow.
  async function answer(s:Segment,species:Taxon|null,verdict:Verdict){answering=true;answerError='';const at=shownAt;
    try{const value=await api<{now:HeardRecord[]}>('/heard/map-answer',{dataset_id:id,recording_id:s.recording_id,start_s:s.start_s,end_s:s.end_s,...species?{species_id:species.id,verdict}:{verdict:'no_bird'}});
      if(pick===s)recorded=value.now;choosing=false;
      // The same rule as the sidecar's marks: the chosen species' answer, or without one any species heard, else "not heard" ("no bird sound" too).
      const verdicts=value.now.filter(r=>wanted?r.species.id===wanted.id:r.species.rank!=='none'||r.verdict==='present').map(r=>r.species.rank==='none'?'absent':r.verdict);
      if(verdicts.includes('present'))markAt.set(at,1);else if(verdicts.includes('absent'))markAt.set(at,2);else markAt.delete(at);
      blit();loadRecorded();entries?.refresh();
    }catch(e){answerError=e instanceof Error?e.message:'unknown_error';}finally{answering=false;}}
  // Species entered on a ticked sound change the map's marks too.
  let pickedNow='';
  $effect(()=>{const now=JSON.stringify(Object.values(picks.state.items).map(i=>[i.recording_id,i.start_s,i.species?.id,i.background.map(b=>b.id)]));
    untrack(()=>{if(pickedNow&&pickedNow!==now){entries?.refresh();loadRecorded();}pickedNow=now;});});
  // "Look here" and the map's flags go into a group. The map is brought into view before the move: the layout keeps
  // the scroll position over the page made anew for the group, so the map is in view there and stays.
  function enter(target:number){document.querySelector('.explore')?.scrollIntoView({block:'start'});void go(target);}
  const tally=$derived.by(()=>{const items=Object.values(picks.state.items);return {n:items.length,blanks:blanks(items)};});
  const recent=$derived(recentSpecies(Object.values(picks.state.items)));
  const job=$derived(activeJob());
  const building=$derived(job?.stage.startsWith('explore_')&&datasetOf(job.dataset_id)?.site_id===datasetOf(id)?.site_id?job:null);
  const ended=$derived(app.jobs.find(j=>j.id===watched&&(j.status==='failed'||j.status==='cancelled')));
  // Why a build stopped; a full drive is reported as `explore_disk_space:<needed bytes>:<free bytes>`,
  // missing recordings as `source_missing:<how many more>:<first missing path>` (the path may contain ':').
  const why=(code:string)=>{const [key,need,free]=code.split(':'),size=(bytes:string)=>new Intl.NumberFormat(app.locale,{style:'unit',unit:'gigabyte',maximumFractionDigits:1}).format(Number(bytes)/1e9);
    if(key==='source_missing'&&need)return fill('source_missing_path',{path:code.split(':').slice(2).join(':'),more:Number(need)?fill('sourceMissingMore',{n:need}):''});
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
        selected=null;open={};pickAt=-1;pick=null;quietUntil=performance.now()+400;inGroup=[];loadRecorded();
        const kept=draft?.created_at===next.created_at?draft:null,saved=kept?.node===next.node.node?kept.loaded??{}:{};draft=null;
        // The sound chosen before going inside (or before leaving the page) stays chosen when it is in this group, without playing again;
        // a sound clicked outside the group that was viewed plays, as any clicked sound.
        const was=carry>=0?carry:kept?.pick??-1,play=carry>=0;carry=-1;
        if(was>=next.node.lo&&was<next.node.hi){const at=(next.children??[]).findIndex(c=>c.lo<=was&&was<c.hi);selected=at<0?null:at;void choose(was,play);
          // Going inside with a chosen sound opens the smaller group it belongs to, without scrolling away from the map.
          if(at>=0&&!Object.keys(saved).length)open[next.children![at].node]=true;}
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
    const anchor=anchorOf(),near=pickAt,group=[node,...children].find(g=>g?.node===at),first=group?matching(group):[];
    lists[at]??={total:0,segments:[],pending:false,error:'',anchor};
    const list=lists[at],sorted=list.anchor!==anchor;
    if(list.pending||!sorted&&list.segments.length>=Math.max(1,Math.min(count,list.total)))return;
    list.pending=true;list.error='';
    // A new order loads aside and replaces the shown sounds at once, so the rows never shrink while loading.
    const segments=sorted?[]:[...list.segments];let total=list.total;
    try{do{const next=await api<Page>(`/explore/nodes/${at}/segments?dataset=${id}&offset=${segments.length}${near>=0?`&near=${near}`:''}`,first.length?{first}:undefined);
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
  // Ticking a sound collects it with nothing filled in; unticking removes it and its labels.
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
  const heardAt=(local:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:site?.timezone}).format(new Date(local));
  const when=(s:Segment)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:s.timezone}).format(new Date(Date.parse(s.recorded_at)+s.start_s*1000));
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
  // The candidates are birds of the Japan checklist only. Where the tag beside them names the AI's answer and that
  // species is not among them (a bird not usual here, or one off the checklist), the line says so; else the two seem to disagree.
  const besides=(s:Segment)=>s.position!==undefined&&targetAt.has(s.position)&&(overlay.entry==='unsure'
    ||overlay.entry==='species'&&!!wanted&&wanted.rank!=='family'&&!s.candidates.some(c=>c.scientific_name===wanted!.scientific_name));
  const candidates=(s:Segment)=>s.candidates.length?fill(besides(s)?'eCandidatesJapan':'eCandidates',{list:listOf(s.candidates,c=>c.score)}):besides(s)?'':t('eNoCandidates');
  const made=(at:string|null)=>at?new Intl.DateTimeFormat(app.locale,{dateStyle:'medium'}).format(new Date(`${at.replace(' ','T')}Z`)):'';

  // Scatter: every point is drawn once into an offscreen canvas; pan and zoom move that image and redraw sharply when the gesture pauses.
  let points:Points|null=null,canvas:HTMLCanvasElement|null=null,offscreen:HTMLCanvasElement|null=null,timer:ReturnType<typeof setTimeout>|undefined,animation=0;
  // Device pixels: screen = base * k + t, where base fits all points into the canvas.
  let view={k:1,tx:0,ty:0},drawn={k:1,tx:0,ty:0},drag:{x:number;y:number;tx:number;ty:number;moved:boolean}|null=null;
  // Where each child's numbered badge goes: the median of its points, in map coordinates.
  let marks:{x:number;y:number}[]=[];
  // From the ways into the map: answers given by ear, the sounds the open way points at (its targets) and the groups it suggests.
  let overlay=$state.raw<MapOverlay>({entry:'unsure',marks:[],targets:[],groups:[],guess:new Map()});
  // The same, by position: the mark of each answered sound (changed at once by an answer on this page) and which sounds are targets.
  let markAt=new Map<number,1|2>();
  const targetAt=$derived(new Set(overlay.targets));
  const matching=(g:{lo:number;hi:number})=>overlay.targets.filter(i=>i>=g.lo&&i<g.hi);
  // The whole map is the overview: its points keep their group colours and the suggested groups carry flags. Inside a group
  // (or on a map so small that the whole of it is the suggestion) the group's targets are drawn a little larger.
  const inside=$derived(!!node&&(node.node!==0||overlay.groups.some(g=>g.node===0))&&matching(node).length>0);
  // Suggested groups get a flag, lettered as in the list; not the group being viewed or one around it: the user is there already.
  const offered=$derived(node?overlay.groups.map((g,index)=>({...g,letter:'ABCDEFGHIJ'[index]})).filter(g=>!(g.lo<=node.lo&&node.hi<=g.hi)):[]);
  // What the open way says about a sound, shown beside it in the lists and under the map.
  const badge=(at:number|undefined)=>{if(at===undefined||!targetAt.has(at))return '';const guess=overlay.guess.get(at);
    return overlay.entry==='examples'?t('meBadge_examples'):overlay.entry==='species'?wanted?fill('meBadge_species',{name:taxonName(wanted)}):''
      :guess?fill('meBadge_unsure',{name:commonName(guess,app.locale)||guess.scientific_name}):'';};
  // Each flag in map coordinates: the middle of the group's points and half the width and height of their middle 90%, so a few far points do not stretch it.
  let flags:{node:number;letter:string;x:number;y:number;rx:number;ry:number}[]=[];
  function plant(){const all=points;flags=!all?[]:offered.map(g=>{
    const span=(values:Float32Array)=>{const sorted=values.slice(g.lo,g.hi).sort(),a=sorted[Math.floor(sorted.length*.05)],b=sorted[Math.ceil(sorted.length*.95)-1];return [(a+b)/2,(b-a)/2];};
    const [x,rx]=span(all.x),[y,ry]=span(all.y);return {node:g.node,letter:g.letter,x,y,rx,ry};});}
  // On the canvas: a dashed ring around the group and the lettered badge on its upper right.
  const ring=(flag:typeof flags[number],{ratio,x0,y0,sx,sy,ox,oy}:ReturnType<typeof frame>)=>{
    const cx=ox+(flag.x-x0)*sx,cy=oy-(flag.y-y0)*sy,rx=1.4*flag.rx*sx+12*ratio,ry=1.4*flag.ry*sy+12*ratio;const r=13*ratio,keep=(v:number,size:number)=>Math.min(Math.max(v,r+2*ratio),size-r-2*ratio);
    // A group at the map's edge keeps its letter inside the picture.
    return {cx,cy,rx,ry,bx:keep(cx+.7*rx,canvas!.width),by:keep(cy-.7*ry,canvas!.height),r};};
  function overlayed(value:MapOverlay){const before=overlay.targets.join();overlay=value;markAt=new Map(value.marks);if(before!==value.targets.join())order++;plant();render();}
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
    marks=children.map(c=>({x:median(x.subarray(c.lo,c.hi)),y:median(y.subarray(c.lo,c.hi))}));plant();
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
  // Inside a group, the sounds the open way points at are a little larger, in their group's own colour with a thin white edge.
  function render(){
    if(!points||!canvas||!offscreen||!node)return;
    const {n,x,y}=points,{ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),context=offscreen.getContext('2d')!;
    offscreen.width=canvas.width;offscreen.height=canvas.height;context.strokeStyle='#fff';context.lineWidth=ratio;
    const dots=(lo:number,hi:number,size:number)=>{for(let i=lo;i<hi;i++)context.fillRect(ox+(x[i]-x0)*sx-size/2,oy-(y[i]-y0)*sy-size/2,size,size);};
    const larger=(lo:number,hi:number)=>{if(inside)for(let i=lo;i<hi;i++)if(targetAt.has(i)){context.beginPath();context.arc(ox+(x[i]-x0)*sx,oy-(y[i]-y0)*sy,3.5*ratio,0,2*Math.PI);context.fill();context.stroke();}};
    const paint=(index:number,draw:(lo:number,hi:number)=>void)=>{context.globalAlpha=selected===null?.8:index===selected?.9:.15;context.fillStyle=palette[index%palette.length];draw(children[index].lo,children[index].hi);};
    context.globalAlpha=.1;context.fillStyle=getComputedStyle(canvas).color;dots(0,node.lo,2*ratio);dots(node.hi,n,2*ratio);
    if(!children.length){context.globalAlpha=.8;context.fillStyle=palette[0];dots(node.lo,node.hi,4*ratio);larger(node.lo,node.hi);}
    // The chosen smaller group is drawn last, and every larger dot over the small ones.
    const others=children.map((_,index)=>index).filter(index=>index!==selected),all=selected!==null&&children[selected]?[...others,selected]:others;
    for(const index of all)paint(index,(lo,hi)=>dots(lo,hi,3*ratio));
    for(const index of all)paint(index,larger);
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
    const at=(i:number)=>[ox+(points!.x[i]-x0)*sx,oy-(points!.y[i]-y0)*sy] as const,ink=getComputedStyle(canvas).color;
    // The open way's flags: the letter matches the list of suggestions.
    context.font=`700 ${14*ratio}px ${getComputedStyle(canvas).fontFamily}`;
    for(const flag of flags){const {cx,cy,rx,ry,bx,by,r}=ring(flag,frame(canvas,points));
      if(cx<0||cx>canvas.width||cy<0||cy>canvas.height)continue;
      context.beginPath();context.ellipse(cx,cy,rx,ry,0,0,2*Math.PI);context.setLineDash([5*ratio,4*ratio]);context.lineWidth=1.5*ratio;context.strokeStyle=ink;context.stroke();context.setLineDash([]);
      context.beginPath();context.arc(bx,by,r,0,2*Math.PI);context.fillStyle=ink;context.fill();context.fillStyle=getComputedStyle(canvas).backgroundColor;context.fillText(flag.letter,bx,by);}
    // What was heard by ear: a blue dot with a tick (heard) or a grey one with a cross (not heard), told apart without colour.
    const mark=(cx:number,cy:number,state:1|2,r:number)=>{context.beginPath();context.arc(cx,cy,r,0,2*Math.PI);context.fillStyle=state===1?HEARD:NOT_HEARD;context.fill();
      context.lineWidth=.25*r;context.strokeStyle='#fff';context.stroke();context.beginPath();
      if(state===1){context.moveTo(cx-.5*r,cy);context.lineTo(cx-.12*r,cy+.38*r);context.lineTo(cx+.5*r,cy-.32*r);}
      else{context.moveTo(cx-.38*r,cy-.38*r);context.lineTo(cx+.38*r,cy+.38*r);context.moveTo(cx+.38*r,cy-.38*r);context.lineTo(cx-.38*r,cy+.38*r);}
      context.stroke();};
    for(const [i,state] of markAt)if(i<points.n)mark(...at(i),state,7*ratio);
    // The sound shown under the map is the largest dot, with a dark outline on a white one so it stands out on any colour.
    if(pickAt>=0&&pickAt<points.n){const [cx,cy]=at(pickAt),state=markAt.get(pickAt),r=9*ratio;
      if(state)mark(cx,cy,state,r);
      else{context.beginPath();context.arc(cx,cy,r,0,2*Math.PI);context.fillStyle=palette[Math.max(0,children.findIndex(c=>c.lo<=pickAt&&pickAt<c.hi))%palette.length];context.fill();}
      for(const [width,colour] of [[5.5,'#fff'],[2.5,ink]] as const){context.beginPath();context.arc(cx,cy,r+2*ratio,0,2*Math.PI);context.lineWidth=width*ratio;context.strokeStyle=colour;context.stroke();}}
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
  // A click on a flag goes into its group. Else it picks the nearest point (within 8 px), shows and plays its sound and highlights its smaller group without scrolling;
  // a second click within 350 ms goes into it. A point outside the group being viewed moves to its group first.
  function up(event:PointerEvent){
    const click=drag&&!drag.moved;drag=null;
    // Clicks right after going into a group are ignored, so a fast double click cannot go down two levels.
    if(!click||!points||!canvas||!node||performance.now()<quietUntil)return;
    const now=performance.now(),double=now-lastClick<350;lastClick=now;
    const {ratio,x0,y0,sx,sy,ox,oy}=frame(canvas,points),bounds=canvas.getBoundingClientRect();
    const px=(event.clientX-bounds.left)*ratio,py=(event.clientY-bounds.top)*ratio;
    const hit=flags.find(flag=>{const {cx,cy,bx,by,r}=ring(flag,frame(canvas!,points!));return cx>=0&&cx<=canvas!.width&&cy>=0&&cy<=canvas!.height&&Math.hypot(bx-px,by-py)<=r;});
    if(hit){enter(hit.node);return;}
    let best=-1,limit=(8*ratio)**2;
    for(let i=0;i<points.n;i++){const dx=ox+(points.x[i]-x0)*sx-px,dy=oy-(points.y[i]-y0)*sy-py,d=dx*dx+dy*dy;if(d<limit){limit=d;best=i;}}
    const child=children.findIndex(c=>c.lo<=best&&best<c.hi);
    if(double&&child>=0){void go(children[child].node);return;}
    // An empty spot clears the pick.
    if(best<0){selected=null;pickAt=-1;pick=null;return;}
    if(best<node.lo||best>=node.hi){carry=best;api<{node:number}>(`/explore/points/${best}/group?dataset=${id}&node=${node.node}`).then(group=>go(group.node),()=>{carry=-1;});return;}
    if(best!==pickAt||pickError)void choose(best);
    if(child>=0)show(child);
  }
</script>
<!-- In the list the line wraps, and the row of the current sound is marked. -->
{#snippet about(s:Segment,current=false)}<span class="about" title={`${when(s)} · ${candidates(s)}`}>{#if current}<span class="current">{t('eCurrentMark')}</span>{' '}{/if}{when(s)}{#if candidates(s)} · <span class="muted">{candidates(s)}</span>{/if}{#if badge(s.position)}{' '}<span class="tag">{badge(s.position)}</span>{/if}</span>{/snippet}
{#snippet sounds(g:Group)}
  {@const at=g.node}
  {@const list=lists[at]}
  {@const first=matching(g).length}
  {#if first}<p class="muted">{fill(`meFirst_${overlay.entry}`,{n:first})}</p>{/if}
  {#each list?.segments??[] as s (s.segment_id)}{@const p=picks.state.items[pickKey(s)]}<div class="member" class:picked={!!p}>
    <label class="check"><input type="checkbox" aria-label={`${t('ePick')}: ${when(s)}`} checked={!!p} onchange={event=>pickSound(s,event.currentTarget.checked)}/>{t('ePick')}</label>
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
      <p class="muted">{fill('eMadeFrom',{date:made(data.created_at),model:modelIntro(data.engine),n:data.segment_count.toLocaleString(app.locale)})}</p>
      <MapEntries bind:this={entries} datasetId={id} node={node.node} go={enter} onoverlay={overlayed} onspecies={value=>wanted=value}/>
      <div class="explore">
        <section class="panel stack map-panel" aria-label={t('eMapHeading')}>
          {#if pointsError}<p class="error" role="alert">{t(pointsError)}</p>{/if}
          <!-- The list is the keyboard path to the same groups; the map adds pointer zoom, pan, picking and double-click to go inside. -->
          <!-- The controls sit above the map, never over its points and group numbers. -->
          {#if inside}<p class="info" role="status">{t(`meHint_${overlay.entry}`)}</p>{:else if offered.length}<p class="info" role="status">{t('meHintFlags')}</p>{/if}
          <div class="map-nav" role="group" aria-label={t('eMapNav')}>
            <Button size="sm" disabled={!canBack} onclick={()=>step(-1)}>{t('eBackView')}</Button>
            <Button size="sm" disabled={!canForward} onclick={()=>step(1)}>{t('eForwardView')}</Button>
            <Button size="sm" disabled={!node.node&&!transformed} onclick={whole}>{t('eTop')}</Button>
          </div>
          <canvas use:scatter aria-label={t('eMapLabel')} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={()=>drag=null}></canvas>
          <!-- One legend for the whole map: size and outline (and the tick and cross) carry the meaning, colour only helps. -->
          <p class="legend">{#if offered.length}<span><i class="flag"></i>{t('meLegendFlag')}</span>{/if}
            {#if inside}<span><i class="target" style:background={palette[0]}></i>{t(`meLegendTarget_${overlay.entry}`)}</span><span><i class="other" style:background={palette[0]}></i>{t('meLegendOther')}</span>{/if}
            {#if node.node}<span><i class="other"></i>{t('meLegendOutside')}</span>{/if}
            <span><i class="now"></i>{t('meLegendNow')}</span>
            <span><i class="mark" style:background={HEARD}>✓</i>{t(overlay.entry==='unsure'?'meLegendHeardAny':'hdV_present')}</span><span><i class="mark" style:background={NOT_HEARD}>×</i>{t('hdV_absent')}</span></p>
          <p class="muted">{fill('eMapHint',{s:+(node.rep.end_s-node.rep.start_s).toFixed(1)})}</p>
          <!-- Fixed text lines and a kept-up card: a new pick changes what the card shows, never how tall it is. -->
          <div class="pick" aria-busy={pickAt>=0&&!pickError&&shownAt!==pickAt}>
            {#if pickError}<p class="error" role="alert">{t(pickError)}</p>
            {:else if !pick}{#if pickAt<0}<p class="muted">{t('ePickHint')}</p>{:else}<p role="status"><Spinner/> {t('loading')}</p>{/if}
            {:else}
              {@const s=pick}
              {@const child=children.findIndex(c=>c.lo<=shownAt&&shownAt<c.hi)}
              {@const tag=badge(shownAt)}
              <!-- Its own line above the figure, free to wrap: when, which group, and what the open way says about the sound. -->
              <h3 class="line"><strong>{t('ePicked')}</strong> {when(s)}{#if child>=0}{' · '}{groupName(children[child])}{/if}{#if tag}{' · '}{tag}{/if}{#if shownAt!==pickAt} <Spinner/>{/if}</h3>
              <!-- A new pick mounts a new player; the click on the map is the gesture that lets it start on its own. -->
              {#key s.segment_id}<AudioRegion source={sourceOf(s)} fixedRange compact dense autoplay={autoplayPick} start={s.start_s} end={s.end_s}>{#snippet meta()}{#if candidates(s)}<span class="muted" title={candidates(s)}>{candidates(s)}</span>{/if}{/snippet}</AudioRegion>{/key}
              <div class="answer">
                {#if wanted}{@const mine=recorded.find(r=>r.species.id===wanted!.id)}
                  <strong>{fill('maTitle',{species:taxonName(wanted)})}</strong>
                  <div class="actions" role="group" aria-label={fill('maTitle',{species:taxonName(wanted)})}>{#each ['present','absent','unknown'] as const as verdict}
                    <Button size="sm" active={mine?.verdict===verdict} aria-pressed={mine?.verdict===verdict} disabled={answering} onclick={()=>answer(s,wanted!,verdict)}>{t(`maV_${verdict}`)}</Button>{/each}</div>
                {:else if choosing}
                  <strong>{t('maTitleAny')}</strong>
                  <SpeciesChoice datasetId={id} onchoose={taxon=>answer(s,taxon,'present')}/>
                  <Button size="sm" onclick={()=>choosing=false}>{t('maCancel')}</Button>
                {:else}{@const none=recorded.some(r=>r.species.rank==='none'&&r.verdict==='present')}
                  <strong>{t('maTitleAny')}</strong>
                  <div class="actions"><Button size="sm" disabled={answering} onclick={()=>choosing=true}>{t('maChoose')}</Button>
                    <Button size="sm" active={none} aria-pressed={none} disabled={answering} onclick={()=>answer(s,null,'present')}>{t('ruNotBird')}</Button></div>
                {/if}
                <span class="muted">{recorded.length?fill('maNow',{list:recorded.map(recordText).join(t('eSep'))}):t('maNone')}</span>
                {#if answerError}<span class="error" role="alert">{t(answerError)}</span>{/if}
              </div>
            {/if}
          </div>
        </section>
        <section class="panel stack">
          <nav class="trail" aria-label={t('eTrail')}>
            {#each trail as step,index (step.node)}
              {#if index}<span class="muted" aria-hidden="true">›</span>{/if}
              {#if index<trail.length-1}<a href={here(step.node)} data-sveltekit-replacestate data-sveltekit-noscroll>{groupName(step)}</a>{:else}<strong aria-current="page">{groupName(step)}</strong>{/if}
            {/each}
          </nav>
          <p>{fill('eSummary',{n:node.size.toLocaleString(app.locale),share:pct(node.size/Math.max(1,data.segment_count),1)})}<br><span class="muted">{aiHint(node)}</span></p>
          <!-- Answers given under the map show here at once; the newest five are enough to see that each one was kept. -->
          <h2>{t('eRecorded')}</h2>
          {#if inGroup.length}<ul class="recorded">{#each inGroup.slice(0,5) as r (r.id)}<li>{heardAt(r.local_time)} · {recordText(r)}</li>{/each}</ul>
            {#if inGroup.length>5}<p class="muted">{fill('eRecordedMore',{shown:5,n:inGroup.length})}</p>{/if}{/if}
          <p class="muted">{t('eRecordedHint')}</p>
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
                  {@render sounds(c)}
                </div>{/if}
              </details>
            {/each}
          {:else}
            <h2>{fill('eMembersHeading',{n:node.size.toLocaleString(app.locale)})}</h2>
            {@render orderNote()}
            {@render sounds(node)}
          {/if}
        </section>
      </div>
      {#if picks.state.error}<p class="error" role="alert">{t(picks.state.error)}</p>{/if}
      <!-- Not a step of the page: collecting sounds to hand to others comes after finding, listening and recording, folded. -->
      <details class="panel"><summary>{t('eCollect')}{#if tally.n}{' · '}{fill('eTally',{n:tally.n})}{/if}</summary>
        <div class="stack"><p>{t('eCollectText')}</p>
          {#if tally.n}{#if tally.blanks}<p class="muted">{tally.blanks}</p>{/if}
            <Button href={resolve(`/datasets/${id}/explore/picked`)}>{t('eToPicked')}</Button>
          {:else}<p class="muted">{t('eCollectNone')}</p>{/if}</div></details>
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
  .map-nav{display:flex;gap:4px}
  /* The map takes the height left over by the line about the coloured dots, its 44px controls, the legend, the hint and the current-sound card with their 12px gaps; in a short window it stays 320px and the column scrolls. */
  canvas{display:block;flex:none;width:100%;height:clamp(320px,calc(100vh - 660px),560px);border:1px solid rgb(var(--color-card-border));border-radius:8px;background:rgb(var(--stone-50));touch-action:none;cursor:pointer}
  /* As tall as a shown sound, so showing, changing or clearing one never changes the page height. */
  .answer{display:flex;flex-direction:column;gap:4px;font-size:14px}.answer .actions{gap:6px}
  /* `flex:none`: in the height-limited column the card must not be squeezed under its content, which pushed its first line onto the figure. */
  .pick{display:flex;flex:none;flex-direction:column;gap:8px;min-height:300px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .line{font-size:14px;font-weight:400;line-height:1.6}
  .explore{scroll-margin-top:16px}
  .legend{display:flex;flex-wrap:wrap;gap:4px 16px;font-size:14px}.legend span{display:inline-flex;align-items:center;gap:6px}
  .legend i{box-sizing:border-box;display:inline-grid;place-items:center;width:14px;height:14px;border-radius:50%;font-style:normal;font-size:10px;font-weight:700;line-height:1;color:#fff}
  .legend .target{width:12px;height:12px;border:1px solid #fff}.legend .other{width:7px;height:7px;background:rgb(var(--stone-600)/.35)}
  .legend .flag{border:1.5px dashed currentColor;background:none;color:inherit}
  .legend .now{color:inherit;border:2px solid currentColor;outline:2px solid currentColor;outline-offset:1px;background:none}
  .recorded{margin:0;padding:0;list-style:none}.recorded li{padding:6px 0;border-top:1px solid rgb(var(--stone-300));font-size:14px}
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
  .member{display:grid;grid-template-columns:72px minmax(0,1fr);gap:8px;align-items:center;padding-top:8px;border-top:1px solid rgb(var(--stone-300))}
  .member.picked{margin:0 -12px;padding:8px 12px 12px;background:rgb(var(--primary-500)/.12)}
  /* The labels use the whole row width, from under the check, so their items stay on one line in a narrow column. */
  .labels{grid-column:1/-1}
  /* English labels wrap at the space; Japanese stays one word. */
  .check{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:44px;font-size:14px;font-weight:400;line-height:1.2;text-align:center;cursor:pointer}
  .check input{width:24px;height:24px}
  /* The date and AI suggestions of a listed sound wrap instead of being cut: English needs three lines or more for two or three suggestions. */
  .member .about{white-space:normal}
  /* Outlined, so the mark stays visible on a ticked row's tint; the word, not the colour, carries the meaning. */
  .current{display:inline-block;margin-right:6px;padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;background:rgb(var(--color-card-bg));color:rgb(var(--primary-700));font-size:13px;font-weight:600;line-height:1.5}
  /* What the open way says about a listed sound: outlined words, as the current-sound mark. */
  .tag{display:inline-block;padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;color:rgb(var(--primary-700));font-size:13px;line-height:1.5}
  .empty :global(.button){align-self:center}
  @media(max-width:700px){.flow{grid-template-columns:minmax(0,1fr)}}
  @media(max-width:900px){.explore{grid-template-columns:minmax(0,1fr)}.map-panel{position:static;max-height:none;overflow:visible}canvas{height:320px}}</style>
