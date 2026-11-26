<script lang="ts">
  import { resolve } from '$app/paths';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { fill,modelName,rangeQuery } from '$lib/workflow';
  import { familyName,rollupTitle,speciesName,type RollupAnswer,type RollupFamily,type RollupSound } from '$lib/rollups';
  import type { Species } from '$lib/species';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import ReferencePanel from '$lib/ReferencePanel.svelte';
  import SpeciesPicker from '$lib/SpeciesPicker.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),family=page.params.family!;
  const query=$derived(new URL(page.url.hash.slice(1),page.url).searchParams);
  const dates=$derived(new URLSearchParams([...query].filter(([key])=>key.endsWith('_date'))).toString());
  let data=$state<RollupFamily|null>(null),error=$state(''),index=$state(0),played=$state<string[]>([]),savedNote=$state('');
  let all=$state(false),picking=$state(false),picked=$state<Species|null>(null),comparing=$state(false),compared=$state('');
  const key=(sound:RollupSound)=>`${sound.recording_id}:${sound.start_s}:${sound.end_s}`;
  async function load(){try{data=await api<RollupFamily>(`/datasets/${id}/rollups/${encodeURIComponent(family)}${dates?`?${dates}`:''}`);compared=data.choices[0]?.id??'';index=Math.max(0,data.sounds.findIndex(item=>!item.answer));}
    catch(e){error=e instanceof Error?e.message:'unknown_error';}}
  $effect(()=>{void dates;void load();});
  const range=$derived(data?rangeQuery(data.start_date,data.end_date):dates);
  const back=$derived(`${resolve(`/datasets/${id}`)}?${range}`);
  const sound=$derived(data?.sounds[index]);
  // Empty on the page of the families without a species in Japan, which has no family of its own (and no choices).
  const familyText=$derived(data?.family?familyName(data.family):'');
  const left=$derived(data?.sounds.filter(item=>!item.answer).length??0);
  const listened=$derived(!!sound&&played.includes(key(sound)));
  // Species likely at this site are listed before "N more" (at least five); when none is known to be likely, up to twelve are listed.
  const likely=$derived(data?.choices.filter(choice=>choice.likely).length??0);
  const short=$derived(likely?Math.max(5,likely):12);
  // The species answered for this sound stays visible even when it is folded away or was chosen from all species.
  const shown=$derived(data?(all?data.choices:data.choices.slice(0,Math.max(short,1+data.choices.findIndex(choice=>choice.id===sound?.answer?.species?.id)))):[]);
  const extra=$derived(sound?.answer?.species&&!data?.choices.some(choice=>choice.id===sound.answer!.species!.id)&&sound.answer.species.id!==sound.japan_guess?.id?sound.answer.species:null);
  // The next sound without an answer after this one, going round to the earlier ones.
  const nextOpen=$derived.by(()=>{if(!data)return -1;const n=data.sounds.length;for(let step=1;step<n;step++){const at=(index+step)%n;if(!data.sounds[at].answer)return at;}return -1;});
  const time=(item:RollupSound)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:data?.site.timezone}).format(new Date(item.local_time));
  const answerText=(answer:RollupAnswer)=>answer.kind==='species'&&answer.species?speciesName(answer.species)
    :answer.kind==='family'?fill('ruAnsFamily',{family:familyText}):t(answer.kind==='not_bird'?'ruNotBird':'ruUnknown');
  function go(to:number){savedNote='';index=Math.max(0,Math.min((data?.sounds.length??1)-1,to));picking=false;picked=null;}
  // After an answer the next sound without one comes up; on the last one the page says so and stays.
  const answer=createTask(async(kind:RollupAnswer['kind']|null,species:Species|null=null)=>{if(!data||!sound)return;
    await api('/rollups/answers',{dataset_id:id,recording_id:sound.recording_id,start_s:sound.start_s,end_s:sound.end_s,family:sound.family,kind,species_id:species?.id??null});
    sound.answer=kind?{kind,species}:null;picking=false;picked=null;
    savedNote=kind?fill('ruSaved',{n:index+1,answer:answerText(sound.answer!)}):'';
    if(kind&&nextOpen>=0)index=nextOpen;
  },{toast:true});
  const comparedSpecies=$derived(data?.choices.find(choice=>choice.id===compared));
  const practice=$derived(`${resolve('/quiz')}?species=${compared}&return=${encodeURIComponent(`/datasets/${id}/rollups/${family}?${range}`)}`);
</script>
<div class="page wide">
  <div class="crumbs"><a href={back}>{t('ruBack')}</a></div>
  <div class="page-header"><div>{#if data}<div class="site-name">{data.site.name}</div>{/if}<h1>{data?rollupTitle(data.family):t('ruBoxTitle')}</h1><p>{t('ruCardHint')}</p></div></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if data}
    {#if !sound}<section class="panel"><p>{t('ruNone')}</p><Button variant="primary" href={back}>{t('ruBack')}</Button></section>
    {:else}<div class="with-examples" class:comparing>
      <section class="panel card">
        {#if savedNote}<p class="saved" role="status">✓ {savedNote}</p>{/if}
        <h2>{t('ruAsk')}</h2>
        <p class="muted">{fill('ruPosition',{total:data.sounds.length,n:index+1})}　{time(sound)}　{fill('ruAiAnswer',{name:speciesName(sound.guess)})}　{fill('ruAi',{ai:new Intl.ListFormat(app.locale).format(sound.engines.map(modelName))})}</p>
        {#key key(sound)}<AudioRegion source={{id:sound.recording_id,kind:'recording',path:sound.source_path,seconds:sound.seconds}} fixedRange compact start={sound.start_s} end={sound.end_s}
          onplaying={()=>{if(!played.includes(key(sound)))played=[...played,key(sound)];}}/>{/key}
        {#if sound.answer}<p class="status" role="status"><strong>✓ {fill('ruYourAnswer',{answer:answerText(sound.answer)})}</strong>
          <Button pending={answer.pending} onclick={()=>answer.run(null)}>{t('ruUndo')}</Button></p>
        {:else if !listened}<p class="muted">{t('pPlayFirst')}</p>{/if}
        <p class="muted">{t('ruConfident')}</p>
        {#if !data.choices.length}<p>{data.family?fill('ruNoJapan',{family:familyText}):t('ruNoneNoJapan')}</p>{/if}
        {#if data.choices.length}<h3>{fill('ruChoices',{family:familyText})}</h3>
        <div class="choices">{#each shown as choice (choice.id)}<Button active={sound.answer?.species?.id===choice.id} aria-pressed={sound.answer?.species?.id===choice.id}
            disabled={!listened&&!sound.answer} pending={answer.pending} onclick={()=>answer.run('species',choice)}>{speciesName(choice)}</Button>{/each}
          {#if data.choices.length>short}<button class="link" onclick={()=>all=!all}>{all?t('ruFewer'):fill('ruMore',{n:data.choices.length-short})}</button>{/if}</div>{/if}
        {#if sound.japan_guess||extra}<div class="choices">{#if sound.japan_guess}{@const guess=sound.japan_guess}<span class="muted">{t('ruJapanGuess')}</span>
          <Button active={sound.answer?.species?.id===guess.id} disabled={!listened&&!sound.answer} pending={answer.pending} onclick={()=>answer.run('species',guess)}>{speciesName(guess)}</Button>{/if}
          {#if extra}<Button active aria-pressed={true} disabled>{speciesName(extra)}</Button>{/if}</div>{/if}
        <div class="choices other">
          {#if data.choices.length}<Button active={sound.answer?.kind==='family'} disabled={!listened&&!sound.answer} pending={answer.pending} onclick={()=>answer.run('family')}>{fill('ruFamilyOnly',{family:familyText})}</Button>{/if}
          <Button active={picking} disabled={!listened&&!sound.answer} onclick={()=>picking=!picking}>{t('ruOther')}</Button>
          <Button active={sound.answer?.kind==='not_bird'} disabled={!listened&&!sound.answer} pending={answer.pending} onclick={()=>answer.run('not_bird')}>{t('ruNotBird')}</Button>
          <Button active={sound.answer?.kind==='unknown'} disabled={!listened&&!sound.answer} pending={answer.pending} onclick={()=>answer.run('unknown')}>{t('ruUnknown')}</Button></div>
        <p class="muted">{t('ruUnknownHint')}</p>
        {#if picking}<div class="picker"><SpeciesPicker bind:value={picked} label={t('ruOther')}/>
          {#if picked&&picked.japan===false}<p class="notice" role="alert">{fill('ruNotJapan',{name:speciesName(picked)})}</p>{/if}
          <div class="actions"><Button variant="primary" disabled={!picked} pending={answer.pending} onclick={()=>picked&&answer.run('species',picked)}>{t('ruOtherConfirm')}</Button></div></div>{/if}
        <div class="nav"><Button disabled={index===0} onclick={()=>go(index-1)}>{t('ruPrev')}</Button>
          {#if data.choices.length}<Button active={comparing} aria-pressed={comparing} onclick={()=>comparing=!comparing}>{t(comparing?'ruCompareClose':'ruCompare')}</Button>{/if}
          {#if sound.answer}<Button disabled={index>=data.sounds.length-1} onclick={()=>go(index+1)}>{t('ruNextSound')}</Button>
          {:else}<Button disabled={nextOpen<0} onclick={()=>go(nextOpen)}>{t('ruSkip')}</Button>{/if}</div>
        <p class="status" role="status">{left?fill('ruLeft',{n:left}):t('ruAllDone')}{#if left&&sound.answer&&nextOpen>=0}<Button onclick={()=>go(nextOpen)}>{t('ruToUnanswered')}</Button>{/if}</p>
        {#if !left}<div class="actions"><Button variant="primary" href={back}>{t('ruBack')}</Button></div>{/if}
      </section>
      {#if comparing&&comparedSpecies}<div class="examples-column"><label>{t('ruCompareWith')}<select bind:value={compared}>{#each data.choices as choice (choice.id)}<option value={choice.id}>{speciesName(choice)}</option>{/each}</select></label>
        {#key compared}<ReferencePanel species={comparedSpecies} datasetId={id} {practice}/>{/key}</div>{/if}
    </div>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('ruPreparing')}</p>{/if}
</div>
<style>.with-examples{display:grid;grid-template-columns:minmax(0,1fr);gap:16px;align-items:start}.with-examples.comparing{grid-template-columns:minmax(0,1fr) minmax(300px,380px)}
  .examples-column{position:sticky;top:16px;max-height:calc(100vh - 140px);overflow-y:auto;display:flex;flex-direction:column;gap:8px}
  @media(max-width:950px){.with-examples.comparing{grid-template-columns:minmax(0,1fr)}.examples-column{position:static;max-height:none;overflow:visible}}
  .card{display:flex;flex-direction:column;gap:12px}.card h2,.card h3,.card p{margin:0}.choices{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
  .other{padding-top:12px;border-top:1px solid rgb(var(--stone-300))}.picker{padding:12px;border:1px solid rgb(var(--stone-300));border-radius:8px}
  .nav{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;padding-top:12px;border-top:1px solid rgb(var(--stone-300))}
  .link{background:none;border:0;padding:8px;color:rgb(var(--primary-700));text-decoration:underline;cursor:pointer;font:inherit}.saved{color:rgb(var(--primary-700));font-weight:600}.status{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:center}</style>
