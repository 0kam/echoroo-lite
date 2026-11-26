<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { tick,untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,fileName,siteOf } from '$lib/app.svelte';
  import HeardTable from '$lib/HeardTable.svelte';
  import { recordText,screenName,taxonName,verdictText,type HeardList,type HeardRecord,type HeardSound,type HeardSummary,type Taxon,type Verdict } from '$lib/heard';
  import { fill } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import SpeciesChoice from '$lib/SpeciesChoice.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  // Every sound the user checked by ear, from all screens: counts per species, and the list, where a row is played and
  // put right in place. One set of choices (`choices`) serves a single row and the ticked rows alike.
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  const site=$derived(siteOf(datasetOf(id)));
  const dates=new URLSearchParams([...query].filter(([key])=>key.endsWith('_date'))).toString();
  const back=`${resolve(`/datasets/${id}`)}${dates?`?${dates}`:''}`;
  const PAGE=50;
  let summary=$state<HeardSummary|null>(null),list=$state<HeardList|null>(null),error=$state(''),scope=$state<'site'|'all'>('site');
  let species=$state(query.get('species')??''),verdict=$state(''),screen=$state(''),day=$state(''),order=$state('new'),shown=$state(PAGE);
  let selected=$state<number[]>([]),sound=$state<HeardSound|null>(null),saved=$state('');
  // The opened row (`at`: its id), in which `row` is listened to or, with `fix`, put right; `bulk`: the ticked rows are put right.
  let open=$state<{at:number;row:HeardRecord;fix:boolean}|null>(null),bulk=$state(false),picking=$state<''|'other'|'add'>(''),plays=$state(0),play=$state(false);
  const filters=$derived(new URLSearchParams(Object.entries({species_id:species,verdict,screen,day,order}).filter(([,value])=>value)).toString());
  async function load(){try{const current=filters;
      const [counts,records]=await Promise.all([api<HeardSummary>(`/datasets/${id}/heard`),api<HeardList>(`/datasets/${id}/heard/records?${current}&limit=${shown}`)]);
      if(current!==filters)return;
      summary=counts;list=records;if(species&&!records.options.species.some(item=>item.id===species)){species='';return;}
      selected=selected.filter(item=>records.rows.some(row=>row.id===item));if(!selected.length)bulk=false;
      if(open&&!records.rows.some(row=>row.id===open!.at))open=null;}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}}
  $effect(()=>{void filters;void shown;untrack(()=>{void load();});});
  const key=(row:{recording_id:number;start_s:number;end_s:number})=>`${row.recording_id}:${row.start_s}:${row.end_s}`;
  const time=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:list?.site.timezone}).format(new Date(value));
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',hourCycle:'h23'}).format(new Date(`${value.replace(' ','T')}Z`));
  const dayName=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));
  const rows=$derived(summary?.rows.filter(row=>scope==='all'||row.site.present+row.site.absent+row.site.unknown>0)??[]);
  // Answers of different species cannot be put right together: the choices ask about one species.
  const chosen=$derived(list?.rows.filter(row=>selected.includes(row.id))??[]);
  const chosenSpecies=$derived(chosen[0]?.species??null);
  const oneSpecies=$derived(!!list&&new Set(list.rows.map(row=>row.species.id)).size===1);
  function toggle(row:HeardRecord){selected=selected.includes(row.id)?selected.filter(item=>item!==row.id):[...selected,row.id];}
  function listen(row:HeardRecord){if(open?.at===row.id&&!open.fix){open=null;return;}
    plays++;play=true;  // mounts the player again from this click, so it may play at once
    if(open?.at!==row.id)open={at:row.id,row,fix:false};}
  // `record`: the answer to put right, the row's own or another one on the same sound.
  function ask(row:HeardRecord,record=row){if(open?.at===row.id&&open.fix&&open.row.id===record.id){open=null;return;}
    if(open?.at!==row.id){plays++;play=false;}
    open={at:row.id,row:record,fix:true};bulk=false;picking='';fix.error='';sound=null;
    api<HeardSound>(`/datasets/${id}/heard/sound?recording_id=${row.recording_id}&start_s=${row.start_s}&end_s=${row.end_s}`).then(value=>{if(open?.at===row.id)sound=value;},()=>{/* The choices still work. */});}
  // After an answer the panel closes; focus goes back to the row's "put right" button, or to the list's heading when the row left the list.
  async function done(message:string){const at=open?key(open.row):'';
    saved=message;open=null;bulk=false;picking='';selected=[];await load();await tick();
    (document.querySelector<HTMLElement>(`[data-sound="${at}"] .buttons button:last-child`)??document.getElementById('hd-list'))?.focus();}
  const fix=createTask(async(records:HeardRecord[],action:Verdict|'other'|'add'|'no_bird'|'delete',taxon:Taxon|null=null)=>{
    const result=await api<{changed:number;checks:number}>('/heard/fix',{dataset_id:id,ids:records.map(row=>row.id),action,species_id:taxon?.id??null});
    await done(fill(action==='delete'?'hdDeleted':result.checks?'hdSavedChecks':'hdSaved',{n:result.changed}));
  });
  // "Back to this record": the earlier answer is given again.
  const restore=createTask(async(row:HeardSound['history'][number])=>{
    await api('/heard/fix',{dataset_id:id,ids:[row.current_id],action:row.verdict,species_id:null});
    await done(fill('hdSaved',{n:1}));
  },{toast:true});
  // The species-AI page can come back here.
  const modelHref=(taxon:Taxon)=>`${resolve(`/datasets/${id}/species/${taxon.id}/model`)}?${dates?`${dates}&`:''}return=${encodeURIComponent(`/datasets/${id}/heard${dates?`?${dates}`:''}`)}`;
</script>
<div class="page wide heard-page">
  <div class="crumbs"><a href={back}>{t('uBackResults')}</a></div>
  <div class="page-header"><div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{t('hdTitle')}</h1><p>{t('hdIntro')}</p></div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if summary&&list}
    <section class="panel" aria-labelledby="hd-counts"><h2 id="hd-counts">{t('hdCounts')}</h2>
      <div class="actions" role="group" aria-label={t('hdCounts')}>{#each ['site','all'] as const as name}
        <Button size="sm" active={scope===name} aria-pressed={scope===name} onclick={()=>scope=name}>{t(`hdScope_${name}`)}</Button>{/each}</div>
      {#if rows.length}<div class="table-wrap"><table><thead><tr><th>{t('hdColSpecies')}</th><th class="n">{t('hdV_present')}</th><th class="n">{t('hdV_absent')}</th><th class="n">{t('hdV_unknown')}</th>
        {#if scope==='site'}<th>{t('hdColAi')}</th>{/if}</tr></thead><tbody>
        {#each rows as row (row.species.id)}{@const counts=row[scope]}<tr>
          <td>{#if scope==='site'}<button class="link" onclick={()=>{species=row.species.id;shown=PAGE;document.getElementById('hd-list')?.scrollIntoView();}}>{taxonName(row.species)}</button>{:else}{taxonName(row.species)}{/if}
            <i class="scientific-name">{row.species.scientific_name}</i></td>
          <td class="n">{fill('hdSounds',{n:counts.present})}</td><td class="n">{fill('hdSounds',{n:counts.absent})}</td><td class="n">{fill('hdSounds',{n:counts.unknown})}</td>
          {#if scope==='site'}<td>{#if !row.ai}<span class="muted">{t('hdAiNone')}</span>
            {:else if row.ai.state==='family'}<span class="muted">{t('hdAiFamily')}</span>
            {:else if row.ai.state==='made'}{fill('hdAiMade',{n:row.ai.round})}　<a href={modelHref(row.species)}>{t('hdAiGrow')}</a>
            {:else if row.ai.state==='ready'}{t('hdAiReady')}　<a href={modelHref(row.species)}>{t('hdAiGrow')}</a>
            {:else}{fill('hdAiNeeded',{n:row.ai.needed,m:row.ai.material})}　<a href={modelHref(row.species)}>{t('hdAiGrow')}</a>{/if}</td>{/if}</tr>{/each}
      </tbody></table></div>
      <p class="muted">{fill('hdRule',{total:summary.requirements.total,each:summary.requirements.each})}</p>
      {#if scope==='all'}<p class="muted">{t('hdAllNote')}</p>{/if}
      {:else if !summary.no_bird[scope]}<p>{t('hdEmpty')}</p>{/if}
      {#if summary.no_bird[scope]}<p>{fill('hdNoBirdCount',{n:summary.no_bird[scope]})}</p>{/if}
      <details><summary>{t('hdGuideTitle')}</summary><ul>{#each ['hdGuide1','hdGuide2','hdGuide3','hdGuide4'] as line}<li>{t(line)}</li>{/each}</ul></details>
    </section>
    <section class="panel" aria-labelledby="hd-list"><h2 id="hd-list" tabindex="-1">{fill('hdList',{n:list.total})}</h2><p class="muted">{t('hdListNote')}</p>
      {#if saved}<p class="saved" role="status">✓ {saved}</p>{/if}
      <div class="filters">
        <label>{t('hdColSpecies')}<select bind:value={species} onchange={()=>{shown=PAGE;selected=[];}}><option value="">{t('hdAll')}</option>{#each list.options.species as item (item.id)}<option value={item.id}>{taxonName(item)}</option>{/each}</select></label>
        <label>{t('hdFilterVerdict')}<select bind:value={verdict} onchange={()=>shown=PAGE}><option value="">{t('hdAll')}</option>{#each ['present','absent','unknown'] as const as item}<option value={item}>{verdictText(item)}</option>{/each}</select></label>
        <label>{t('hdFilterScreen')}<select bind:value={screen} onchange={()=>shown=PAGE}><option value="">{t('hdAll')}</option>{#each list.options.screens as item}<option value={item}>{screenName(item)}</option>{/each}</select></label>
        <label>{t('hdFilterDay')}<select bind:value={day} onchange={()=>shown=PAGE}><option value="">{t('hdAll')}</option>{#each list.options.days as item}<option value={item}>{dayName(item)}</option>{/each}</select></label>
        <label>{t('hdOrder')}<select bind:value={order}><option value="new">{t('hdOrder_new')}</option><option value="old">{t('hdOrder_old')}</option><option value="time">{t('hdOrder_time')}</option></select></label>
      </div>
      {#if list.rows.length}<div class="bar" role="group" aria-label={t('hdSelectGroup')}>
        {#if chosen.length&&chosenSpecies}<strong>{fill('hdSelected',{name:taxonName(chosenSpecies),n:chosen.length})}</strong>
          <Button variant="primary" active={bulk} aria-expanded={bulk} onclick={()=>{bulk=!bulk;open=null;picking='';fix.error='';}}>{t('hdFixSelected')}</Button><Button onclick={()=>{selected=[];bulk=false;}}>{t('hdSelectNone')}</Button>
          <span class="muted">{t('hdSelectSame')}</span>
        {:else}<span class="muted">{t('hdSelectHint')}</span>{/if}
        {#if oneSpecies&&chosen.length<list.rows.length}<Button onclick={()=>selected=list!.rows.map(row=>row.id)}>{t('hdSelectAll')}</Button>{/if}</div>{/if}
      {#if bulk&&chosen.length}<div class="sound"><h3>{fill('hdFixTitle',{name:taxonName(chosen[0].species),n:chosen.length})}</h3>{@render choices(chosen)}</div>{/if}
      {#each list.rows as row (row.id)}{@const other=!!chosenSpecies&&chosenSpecies.id!==row.species.id}{@const here=open?.at===row.id?open:null}
      <article class="record" class:selected={selected.includes(row.id)} data-sound={key(row)}>
        <div class="record-head"><label class="check" title={other?t('hdSelectSame'):undefined}><input type="checkbox" checked={selected.includes(row.id)} disabled={other} onchange={()=>toggle(row)}/>
            <strong>{recordText(row)}</strong></label>
          <span>{time(row.local_time)}</span><span class="muted">{screenName(row.screen,row.extra)}</span>{#if row.test}<span class="test" title={t('tsBadgeHint')}>{t('tsBadge')}</span>{/if}
          <span class="buttons"><Button size="sm" active={!!here&&!here.fix} aria-expanded={!!here&&!here.fix} onclick={()=>listen(row)}>{t('hdListen')}</Button>
            <Button size="sm" active={!!here?.fix} aria-expanded={!!here?.fix} onclick={()=>ask(row)}>{t('hdFix')}</Button></span></div>
        {#if here}<div class="sound">
          {#key plays}<AudioRegion source={row.source} fixedRange compact autoplay={play} start={row.start_s} end={row.end_s}/>{/key}
          {#if here.fix}
            {#if sound&&sound.now.length>1}<h3>{t('hdSoundNow')}</h3><ul>{#each sound.now as item (item.id)}<li>{recordText(item)}
              {#if item.id===here.row.id}<span class="muted">{t('hdFixing')}</span>{:else}<Button size="sm" onclick={()=>ask(row,item)}>{t('hdFix')}</Button>{/if}</li>{/each}</ul>{/if}
            {#if row.test||sound?.now.some(item=>item.test)}<p class="muted">{t('tsBadgeHint')}</p>{/if}
            {@render choices([here.row])}
            {#if sound}<details><summary>{fill('hdHistory',{n:sound.history.length})}</summary><ul>{#each sound.history as item (item.id)}<li><span class="muted">{when(item.created_at)}</span>
              <span>{recordText(item)}</span><span class="muted">{screenName(item.screen,item.extra)}</span>{#if item.test}<span class="test" title={t('tsBadgeHint')}>{t('tsBadge')}</span>{/if}
              {#if item.current}<span class="muted">{t('hdNow')}</span>{:else if item.species.rank!=='none'&&sound.history.find(now=>now.id===item.current_id)?.verdict!==item.verdict}<Button size="sm" pending={restore.pending} onclick={()=>restore.run(item)}>{t('hdRestore')}</Button>{/if}</li>{/each}</ul></details>{/if}
          {/if}</div>{/if}
      </article>{:else}<p>{t(list.total||species||verdict||screen||day?'hdNoMatch':'hdEmpty')}</p>{/each}
      <p class="muted">{fill('hdShown',{total:list.total,n:list.rows.length})}
        {#if list.rows.length<list.total}<Button onclick={()=>shown+=PAGE}>{fill('hdMore',{n:Math.min(PAGE,list.total-list.rows.length)})}</Button>{/if}</p>
    </section>
    <HeardTable datasetId={id} onchange={load}/>
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
{#snippet choices(records:HeardRecord[])}{@const taxon=records[0].species}{@const name=taxonName(taxon)}{@const now=records.every(row=>row.verdict===records[0].verdict)?records[0].verdict:null}
  <p>{t('hdFixAsk')}</p>
  <div class="choices" role="group" aria-label={t('hdFixAsk')}>
    {#if taxon.rank==='none'}<Button active aria-pressed="true" disabled={fix.pending} onclick={()=>{open=null;bulk=false;}}>✓ {name}</Button>
      <Button active={picking==='other'} aria-expanded={picking==='other'} disabled={fix.pending} onclick={()=>picking=picking?'':'other'}>{t('hdNoBirdOther')}</Button>
    {:else}
      {#each ['present','absent','unknown'] as const as item}
        <Button active={now===item} aria-pressed={now===item} disabled={fix.pending} onclick={()=>{if(now===item){open=null;bulk=false;}else void fix.run(records,item);}}>{now===item?'✓ ':''}{fill('hdFixTo',{name,verdict:verdictText(item)})}</Button>{/each}
      <Button active={picking==='other'} aria-expanded={picking==='other'} disabled={fix.pending} onclick={()=>picking=picking==='other'?'':'other'}>{t('hdFixOther')}</Button>
      <Button active={picking==='add'} aria-expanded={picking==='add'} disabled={fix.pending} onclick={()=>picking=picking==='add'?'':'add'}>{t('hdFixAdd')}</Button>
      <Button pending={fix.pending} onclick={()=>fix.run(records,'no_bird')}>{t('ruNotBird')}</Button>
    {/if}</div>
  {#if picking}<div class="pick"><p class="muted">{fill(taxon.rank==='none'?'hdNoBirdOtherNote':picking==='add'?'hdFixAddNote':taxon.rank==='family'?'hdFixOtherFamily':'hdFixOtherNote',{name})}</p>
    <SpeciesChoice datasetId={id} exclude={[taxon.id]} pending={fix.pending} onchoose={found=>fix.run(records,picking||'other',found)}/></div>{/if}
  {#if taxon.rank!=='none'}<p class="muted">{fill('hdFixHint',{name})}</p>{/if}
  {#if fix.error}<p class="error" role="alert">{t(fix.error)}</p>{/if}
  <details><summary>{t('hdFixDelete')}</summary><p class="muted">{t('hdFixDeleteNote')}</p>
    <p>{fill('hdDeleteAsk',{n:records.length})}</p><Button variant="danger" pending={fix.pending} onclick={()=>fix.run(records,'delete')}>{t('hdDeleteYes')}</Button></details>
{/snippet}
<style>.test{padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;color:rgb(var(--primary-700));font-size:13px;font-weight:600;white-space:nowrap}
  .heard-page h2{margin:0 0 12px}.heard-page p{margin:8px 0}td.n,th.n{text-align:right;white-space:nowrap}table{table-layout:auto}td{vertical-align:middle}
  .link{background:none;border:0;padding:0;color:rgb(var(--primary-700));text-decoration:underline;cursor:pointer;font:inherit;font-weight:700;text-align:left}
  .scientific-name{display:block}.filters{display:flex;flex-wrap:wrap;gap:8px 16px;margin-bottom:12px}.filters label{display:flex;flex-direction:column;gap:4px}
  .bar{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;padding:8px 0}
  .record{padding:10px 0;border-top:1px solid rgb(var(--stone-300))}.record.selected{border-left:4px solid rgb(var(--primary-700));padding-left:12px}
  .record-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center}.check{display:flex;gap:8px;align-items:center;min-height:44px;cursor:pointer}.check input{width:20px;height:20px}
  .buttons{display:flex;gap:8px;margin-left:auto}.sound{margin:8px 0 4px 28px;display:flex;flex-direction:column;gap:8px}.sound h3{margin:8px 0 0;font-size:16px}
  .sound ul{list-style:none;margin:0;padding:0}.sound li{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:center;padding:4px 0}.saved{color:rgb(var(--primary-700));font-weight:600}
  details summary{min-height:44px;padding:10px 0;cursor:pointer}
  .choices{display:flex;flex-wrap:wrap;gap:8px}.pick{padding:4px 0 8px}.sound details p{margin:4px 0 8px}
</style>
