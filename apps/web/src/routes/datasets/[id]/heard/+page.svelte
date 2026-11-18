<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { untrack } from 'svelte';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,fileName,siteOf } from '$lib/app.svelte';
  import { screenName,taxonName,verdictText,type HeardList,type HeardRecord,type HeardSound,type HeardSummary,type Taxon,type Verdict } from '$lib/heard';
  import { fill } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import SpeciesChoice from '$lib/SpeciesChoice.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  // Every sound the user checked by ear, from all screens: counts per species, the list, and putting answers right.
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  const site=$derived(siteOf(datasetOf(id)));
  const dates=new URLSearchParams([...query].filter(([key])=>key.endsWith('_date'))).toString();
  const back=`${resolve(`/datasets/${id}`)}${dates?`?${dates}`:''}`;
  const PAGE=50;
  let summary=$state<HeardSummary|null>(null),list=$state<HeardList|null>(null),error=$state(''),scope=$state<'site'|'all'>('site');
  let species=$state(query.get('species')??''),verdict=$state(''),screen=$state(''),day=$state(''),order=$state('new'),shown=$state(PAGE);
  let selected=$state<number[]>([]),open=$state<HeardRecord|null>(null),sound=$state<HeardSound|null>(null),saved=$state('');
  const filters=$derived(new URLSearchParams(Object.entries({species_id:species,verdict,screen,day,order}).filter(([,value])=>value)).toString());
  async function load(){try{const current=filters;
      const [counts,records]=await Promise.all([api<HeardSummary>(`/datasets/${id}/heard`),api<HeardList>(`/datasets/${id}/heard/records?${current}&limit=${shown}`)]);
      if(current!==filters)return;
      summary=counts;list=records;if(species&&!records.options.species.some(item=>item.id===species)){species='';return;}
      selected=selected.filter(item=>records.rows.some(row=>row.id===item));
      if(open){const still=records.rows.find(row=>row.id===open!.id)??records.rows.find(row=>key(row)===key(open!));if(still)await openSound(still,true);else{open=null;sound=null;}}}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}}
  $effect(()=>{void filters;void shown;untrack(()=>{void load();});});
  const key=(row:{recording_id:number;start_s:number;end_s:number})=>`${row.recording_id}:${row.start_s}:${row.end_s}`;
  const time=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:list?.site.timezone}).format(new Date(value));
  const when=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',hourCycle:'h23'}).format(new Date(`${value.replace(' ','T')}Z`));
  const dayName=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeZone:'UTC'}).format(new Date(`${value}T00:00:00Z`));
  const rows=$derived(summary?.rows.filter(row=>scope==='all'||row.site.present+row.site.absent+row.site.unknown>0)??[]);
  // Answers of different species cannot be put right together: the dialog asks about one species.
  const chosen=$derived(list?.rows.filter(row=>selected.includes(row.id))??[]);
  const chosenSpecies=$derived(chosen[0]?.species??null);
  const oneSpecies=$derived(!!list&&new Set(list.rows.map(row=>row.species.id)).size===1);
  function toggle(row:HeardRecord){selected=selected.includes(row.id)?selected.filter(item=>item!==row.id):[...selected,row.id];}
  async function openSound(row:HeardRecord,keep=false){if(open?.id===row.id&&!keep){open=null;sound=null;return;}
    if(open&&key(open)!==key(row))sound=null;
    open=row;
    try{sound=await api<HeardSound>(`/datasets/${id}/heard/sound?recording_id=${row.recording_id}&start_s=${row.start_s}&end_s=${row.end_s}`);}
    catch{open=null;sound=null;}}
  // The dialog: which answers, and for the two choices that name a species, which one.
  let dialog=$state<HTMLDialogElement>(),fixing=$state<HeardRecord[]>([]),picking=$state<''|'other'|'add'|'delete'>('');
  const fixName=$derived(fixing[0]?taxonName(fixing[0].species):'');
  function ask(records:HeardRecord[]){fixing=records;picking='';fix.error='';dialog?.showModal();}
  const fix=createTask(async(action:Verdict|'other'|'add'|'delete',taxon:Taxon|null=null)=>{
    const result=await api<{changed:number;checks:number}>('/heard/fix',{dataset_id:id,ids:fixing.map(row=>row.id),action,species_id:taxon?.id??null});
    saved=fill(action==='delete'?'hdDeleted':result.checks?'hdSavedChecks':'hdSaved',{n:result.changed});
    dialog?.close();selected=[];fixing=[];await load();
  });
  // "Back to this record": the earlier answer is given again.
  const restore=createTask(async(row:HeardSound['history'][number])=>{
    await api('/heard/fix',{dataset_id:id,ids:[row.current_id],action:row.verdict,species_id:null});
    saved=fill('hdSaved',{n:1});await load();
  },{toast:true});
  const modelHref=(taxon:Taxon)=>`${resolve(`/datasets/${id}/species/${taxon.id}/model`)}${dates?`?${dates}`:''}`;
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
            {:else}{fill('hdAiNeeded',{n:row.ai.needed})}　<a href={modelHref(row.species)}>{t('hdAiGrow')}</a>{/if}</td>{/if}</tr>{/each}
      </tbody></table></div>
      <p class="muted">{fill('hdRule',{total:summary.requirements.total,each:summary.requirements.each})}</p>
      {#if scope==='all'}<p class="muted">{t('hdAllNote')}</p>{/if}
      {:else}<p>{t('hdEmpty')}</p>{/if}
      <details><summary>{t('hdGuideTitle')}</summary><ul>{#each ['hdGuide1','hdGuide2','hdGuide3','hdGuide4'] as line}<li>{t(line)}</li>{/each}</ul></details>
    </section>
    <section class="panel" aria-labelledby="hd-list"><h2 id="hd-list">{fill('hdList',{n:list.total})}</h2>
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
          <Button variant="primary" onclick={()=>ask(chosen)}>{t('hdFixSelected')}</Button><Button onclick={()=>selected=[]}>{t('hdSelectNone')}</Button>
          <span class="muted">{t('hdSelectSame')}</span>
        {:else}<span class="muted">{t('hdSelectHint')}</span>{/if}
        {#if oneSpecies&&chosen.length<list.rows.length}<Button onclick={()=>selected=list!.rows.map(row=>row.id)}>{t('hdSelectAll')}</Button>{/if}</div>{/if}
      {#each list.rows as row (row.id)}{@const other=!!chosenSpecies&&chosenSpecies.id!==row.species.id}<article class="record" class:selected={selected.includes(row.id)}>
        <div class="record-head"><label class="check" title={other?t('hdSelectSame'):undefined}><input type="checkbox" checked={selected.includes(row.id)} disabled={other} onchange={()=>toggle(row)}/>
            <strong>{fill('hdRecord',{name:taxonName(row.species),verdict:verdictText(row.verdict)})}</strong></label>
          <span>{time(row.local_time)}</span><span class="muted">{screenName(row.screen,row.extra)}</span>{#if row.test}<span class="test" title={t('tsBadgeHint')}>{t('tsBadge')}</span>{/if}
          <span class="buttons"><Button size="sm" active={open?.id===row.id} aria-expanded={open?.id===row.id} onclick={()=>openSound(row)}>{t(open?.id===row.id?'hdClose':'hdOpen')}</Button>
            <Button size="sm" onclick={()=>ask([row])}>{t('hdFix')}</Button></span></div>
        {#if open?.id===row.id&&sound}<div class="sound"><p class="muted">{fill('hdSoundAt',{file:fileName(sound.file),from:sound.start_s,to:sound.end_s})}</p>
          <AudioRegion source={sound.source} fixedRange compact start={sound.start_s} end={sound.end_s}/>
          {#if sound.now.length>1}<h3>{t('hdSoundNow')}</h3><ul>{#each sound.now as item (item.id)}<li>{fill('hdRecord',{name:taxonName(item.species),verdict:verdictText(item.verdict)})}
            {#if item.id!==row.id}<Button size="sm" onclick={()=>ask([item])}>{t('hdFix')}</Button>{/if}</li>{/each}</ul>{/if}
          {#if sound.now.some(item=>item.test)}<p class="muted">{t('tsBadgeHint')}</p>{/if}
          <h3>{t('hdHistory')}</h3><ul>{#each sound.history as item (item.id)}<li><span class="muted">{when(item.created_at)}</span>
            <span>{fill('hdRecord',{name:taxonName(item.species),verdict:verdictText(item.verdict)})}</span><span class="muted">{screenName(item.screen,item.extra)}</span>{#if item.test}<span class="test" title={t('tsBadgeHint')}>{t('tsBadge')}</span>{/if}
            {#if item.current}<span class="muted">{t('hdNow')}</span>{:else if sound.history.find(now=>now.id===item.current_id)?.verdict!==item.verdict}<Button size="sm" pending={restore.pending} onclick={()=>restore.run(item)}>{t('hdRestore')}</Button>{/if}</li>{/each}</ul></div>{/if}
      </article>{:else}<p>{t(list.total||species||verdict||screen||day?'hdNoMatch':'hdEmpty')}</p>{/each}
      <p class="muted">{fill('hdShown',{total:list.total,n:list.rows.length})}
        {#if list.rows.length<list.total}<Button onclick={()=>shown+=PAGE}>{fill('hdMore',{n:Math.min(PAGE,list.total-list.rows.length)})}</Button>{/if}</p>
    </section>
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<dialog bind:this={dialog} aria-labelledby="hd-fix-title" onclose={()=>{fixing=[];picking='';}}>
  {#if fixing.length}<h2 id="hd-fix-title">{fill('hdFixTitle',{name:fixName,n:fixing.length})}</h2><p>{t('hdFixChoose')}</p>
    <div class="options">
      {#each (['present','absent','unknown'] as const).filter(item=>fixing.some(row=>row.verdict!==item)) as item}
        <div class="option"><div><strong>{fill('hdFixTo',{name:fixName,verdict:verdictText(item)})}</strong>{#if item==='unknown'}<p class="muted">{t('hdFixUnknownNote')}</p>{/if}</div>
          <Button pending={fix.pending} onclick={()=>fix.run(item)}>{t('hdChoose')}</Button></div>{/each}
      <div class="option"><div><strong>{fill('hdFixOther',{name:fixName})}</strong><p class="muted">{fill(fixing[0].species.rank==='family'?'hdFixOtherFamily':'hdFixOtherNote',{name:fixName})}</p></div>
        <Button active={picking==='other'} aria-expanded={picking==='other'} disabled={fix.pending} onclick={()=>picking=picking==='other'?'':'other'}>{t('hdPickSpecies')}</Button></div>
      {#if picking==='other'}<div class="pick"><SpeciesChoice datasetId={id} exclude={[fixing[0].species.id]} pending={fix.pending} onchoose={taxon=>fix.run('other',taxon)}/></div>{/if}
      <div class="option"><div><strong>{fill('hdFixAdd',{name:fixName})}</strong><p class="muted">{fill('hdFixAddNote',{name:fixName})}</p></div>
        <Button active={picking==='add'} aria-expanded={picking==='add'} disabled={fix.pending} onclick={()=>picking=picking==='add'?'':'add'}>{t('hdAddSpecies')}</Button></div>
      {#if picking==='add'}<div class="pick"><SpeciesChoice datasetId={id} exclude={[fixing[0].species.id]} pending={fix.pending} onchoose={taxon=>fix.run('add',taxon)}/></div>{/if}
      <div class="option"><div><strong>{t('hdFixDelete')}</strong><p class="muted">{t('hdFixDeleteNote')}</p></div>
        <Button active={picking==='delete'} aria-expanded={picking==='delete'} disabled={fix.pending} onclick={()=>picking=picking==='delete'?'':'delete'}>{t('hdDelete')}</Button></div>
      {#if picking==='delete'}<div class="pick confirm" role="alert"><p>{fill('hdDeleteAsk',{n:fixing.length})}</p>
        <div class="actions"><Button variant="danger" pending={fix.pending} onclick={()=>fix.run('delete')}>{t('hdDeleteYes')}</Button><Button disabled={fix.pending} onclick={()=>picking=''}>{t('mCancel')}</Button></div></div>{/if}
    </div>
    {#if fix.error}<p class="error" role="alert">{t(fix.error)}</p>{/if}
    <div class="actions"><Button disabled={fix.pending} onclick={()=>dialog?.close()}>{t('mCancel')}</Button></div>{/if}
</dialog>
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
  dialog{max-width:640px;width:calc(100% - 32px);max-height:calc(100vh - 32px);overflow-y:auto;padding:24px;border:1px solid #ccc;border-radius:12px;background:rgb(var(--stone-50));color:inherit}
  dialog::backdrop{background:rgb(0 0 0 / .4)}dialog h2{margin:0 0 8px}.options{display:flex;flex-direction:column}.option{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .option p{margin:4px 0 0}.option :global(.button){flex-shrink:0;white-space:nowrap}.pick{padding:0 0 12px}.actions{margin-top:12px}</style>
