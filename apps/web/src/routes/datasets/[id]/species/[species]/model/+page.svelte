<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount,tick } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,siteOf } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { appHref,fill,listenLabel,modelIntro,modelName,pageQuery,returnPath,share,versionFull,versionName,type AIVersions,type LabelRow,type SpeciesAI,type TestReport,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import OtherSpecies from '$lib/OtherSpecies.svelte';
  import TestSounds from '$lib/TestSounds.svelte';
  import HeardBox from '$lib/HeardBox.svelte';
  import AiSharing from '$lib/AiSharing.svelte';
  import BorrowAI from '$lib/BorrowAI.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const site=$derived(siteOf(datasetOf(id)));
  const range=new URLSearchParams([...pageQuery(page.url).params].filter(([key])=>key.endsWith('_date')));
  // `info` holds the counts and the AI; the batch changes only when a new batch is loaded, so its rows do not move while answering.
  let info=$state<SpeciesAI|null>(null),batch=$state<LabelRow[]>([]),answered=$state<LabelRow[]>([]),source=$state<SpeciesAI['source']>(null);
  let error=$state(''),skipped:number[]=[],answeredShown=$state(10),confirming=$state(false);
  // `more` opens the sounds again once growing has settled.
  let more=$state(false);
  // Back from adding examples on the search page (the layout restores the scroll to the panel that led there): say so
  // there and show the next sounds, also once growing has settled.
  const added=sessionStorage.getItem('echoroo-examples-added')===speciesId;
  sessionStorage.removeItem('echoroo-examples-added');
  if(added)more=true;
  // Sounds answered "Not sure": left without an answer, so they are never material for the AI and the next batch skips them.
  let unsure=$state<number[]>([]);
  const notSure=async(row:LabelRow)=>{if(row.label)await label.run(row,row.label);if(row.label)return;const on=!unsure.includes(row.segment_id);
    // "Not sure" is kept as a record too, so it shows among the sounds checked by ear; it is no material for the AI.
    try{await api(`/datasets/${id}/species/${speciesId}/labels`,{segment_id:row.segment_id,label:on?'unknown':null});}catch{return;}
    unsure=on?[...unsure,row.segment_id]:unsure.filter(id=>id!==row.segment_id);};
  const fetchState=(query='')=>api<SpeciesAI>(`/datasets/${id}/species/${speciesId}/custom-model?${range}${query}`);
  // Sounds shown and passed over are not offered again until the AI is made again.
  const next=createTask(async()=>{const value=await fetchState(`&batch=true${skipped.map(segment=>`&skip=${segment}`).join('')}`);
    info=value;batch=value.batch??[];answered=value.answered;source=value.source;answeredShown=10;selected=null;});
  onMount(async()=>{await next.run();if(next.error)error=next.error;});
  // A newer version of this site's AI grown at another site (tried on the test sounds here), offered until chosen or set aside.
  let newer=$state<AIVersions['newer']>(null),report=$state<TestReport|null>(null);
  const lookNewer=()=>api<AIVersions>(`/datasets/${id}/species/${speciesId}/ai-versions?${range}`).then(value=>newer=value.newer,()=>newer=null);
  $effect(()=>{if(info?.model&&info.phase!=='collecting'){void info.model.id;void info.model.round;void lookNewer();}else newer=null;});
  const switchTo=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/use`,{dataset_id:id,start_date:info!.start_date,end_date:info!.end_date});
    newer=null;skipped=[];await next.run();},{toast:true});
  const keepNow=createTask(async(modelId:number)=>{await api(`/datasets/${id}/species/${speciesId}/ai-versions/set-aside`,{model_id:modelId});newer=null;},{toast:true});
  const said=(heard:boolean,yes:boolean)=>t(heard?(yes?'vnSaid_found':'vnSaid_missed'):(yes?'vnSaid_false':'vnSaid_right'));
  // How an AI brought from elsewhere does here against where it was made: more, fewer or about as many misses and false "yes".
  const compare=(here:number,there:number)=>t(Math.abs(here-there)<.05?'vbSame':here>there?'vbMore':'vbLess');
  const label=createTask(async(row:LabelRow,value:'target'|'other')=>{
    const saved=await api<{label:LabelRow['label']}>(`/datasets/${id}/species/${speciesId}/labels`,{segment_id:row.segment_id,label:row.label===value?null:value});
    row.label=saved.label;if(saved.label)unsure=unsure.filter(id=>id!==row.segment_id);info=await fetchState();
    // The last test sound needed: the sounds give way to "Start growing", so bring it into view.
    if(info.phase==='collecting'&&info.test.complete)document.getElementById('test-title')?.scrollIntoView({block:'start'});
    // A sound answered in this batch joins the answered list at once, as the same row, so both show the same answer.
    if(saved.label&&!answered.includes(row))answered.unshift(row);
  },{toast:true});
  // The period the server used for this page, so a link without dates still makes the AI for what is shown.
  const build=createTask(async()=>{await api('/custom-models',{dataset_id:id,species_id:speciesId,
    start_date:info!.start_date,end_date:info!.end_date});skipped=[];await next.run();
    // The new AI's score on the test sounds is above the sounds: bring it into view.
    (document.getElementById('tests-title')??document.getElementById('next-sounds'))?.scrollIntoView({block:'start'});
    notify('mdBuilt','success',undefined,{n:info?.model?.round??1});});
  // A new batch is answered from its first sound, so bring the top of the list back into view.
  const toTop=()=>document.getElementById('next-sounds')?.scrollIntoView({block:'start'});
  const another=async()=>{skipped.push(...batch.filter(row=>!row.label).map(row=>row.segment_id));await next.run();toTop();};
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const remove=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/delete`,{});confirming=false;skipped=[];await next.run();},{toast:true});
  const model=$derived(info?.model??null);
  // The name of the AI the site uses now, as a version: a copy is named for where it was made.
  const nowName=$derived(model?model.imported?versionName(model.imported):versionName({site:site?.name??null,round:model.round}):'');
  const listHref=$derived(`${resolve(`/datasets/${id}/species/${speciesId}/ais`)}?${range}`);
  // Test sounds are made first; growing starts once they are frozen ('fixed'). 'skipped' is a species grown before that rule.
  const collecting=$derived(info?.phase==='collecting');
  const startGrowing=createTask(async()=>{await api(`/datasets/${id}/species/${speciesId}/test-phase`,{});skipped=[];await next.run();},{toast:true});
  // The words for how far it has grown: one round that hardly changed it (growth_state's 0.9) is "almost", not "changed".
  const growText=$derived(model?.growth.state==='growing'&&(model.growth.rounds.at(-1)?.overlap??0)>=.9?'almost':model?.growth.state);
  const folded=$derived(model?.growth.state==='settled'&&!more);
  // The sounds to answer are on the page: not once growing has settled, nor when the test sounds are complete.
  const listed=$derived(!!info&&!folded&&!(collecting&&info.test.complete));
  // One of the next sounds is selected, and the keys act on it: ↑ / ↓ move it, Space plays or stops it, 1 / 2 / 3 answer.
  // Until a row is chosen it is the first sound without an answer. Keys go by their place on the keyboard, as in the accuracy check.
  let selected=$state<number|null>(null);
  const open=(row:LabelRow)=>!row.label&&!unsure.includes(row.segment_id);
  const current=$derived(batch.find(row=>row.segment_id===selected)??batch.find(open)??batch[0]);
  const KEYS:Record<string,'target'|'other'|'unknown'>={Digit1:'target',Numpad1:'target',Digit2:'other',Numpad2:'other',Digit3:'unknown',Numpad3:'unknown'};
  async function shortcut(event:KeyboardEvent){
    // Not while typing in a field, in a dialog, or inside a folded part (its summary, the answered sounds).
    if(event.ctrlKey||event.metaKey||event.altKey||!listed||!current||(event.target as HTMLElement|null)?.closest('input,textarea,select,[contenteditable],dialog,details'))return;
    const row=current,at=batch.indexOf(row),verdict=KEYS[event.code],move=event.code==='ArrowDown'?1:event.code==='ArrowUp'?-1:0;
    if(event.code==='Space'){event.preventDefault();document.querySelector<HTMLElement>('.row[aria-current] .region-controls button')?.click();return;}
    if(!verdict&&!move)return;
    event.preventDefault();
    let to=move?batch[at+move]:undefined;
    if(verdict){if(label.pending||next.pending||build.pending)return;const fresh=open(row);
      await(verdict==='unknown'?notSure(row):label.run(row,verdict));
      // A new answer moves on to the next sound without one; undoing or changing an answer stays on the sound.
      if(fresh&&!open(row))to=batch.slice(at+1).find(open);}
    if(to){selected=to.segment_id;await tick();document.querySelector('.row[aria-current]')?.scrollIntoView({block:'nearest'});}
  }
  // Step 3: this AI's accuracy check, shown here as adding test sounds (an unfinished one with the same AI and period is
  // reused). `measured`: the check of the AI as it is now, with an answer in it; starting one asks first (`asking`),
  // since making the AI again afterwards means measuring again.
  const measured=$derived(info?.check?.applicable&&info.check.counts.answered?info.check:null);
  let asking=$state(false);
  // Back from the check page: the scores, where its result is, come into view.
  const returned=sessionStorage.getItem('echoroo-measuring')===speciesId;
  sessionStorage.removeItem('echoroo-measuring');
  const startCheck=createTask(async()=>{sessionStorage.setItem('echoroo-measuring',speciesId);const validation=await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,
    start_date:info!.start_date,end_date:info!.end_date,model:'custom',model_id:model!.id});await goto(`${resolve(`/datasets/${id}/validation/${validation.id}`)}?return=${encodeURIComponent(here)}`);},{toast:true});
  // Nothing to rank by and nothing to make an AI from yet: the page only points to the search.
  const chosen=$derived(source==='custom'?`${t('mdIntroCustom')}${info!.by_example?` ${t('mdIntroExamples')}`:''}`:source==='search'?t('mdIntroSearch'):source?fill('mdIntroBuiltin',{model:modelIntro(info!.engine)}):'');
  const searchFirst=$derived(!!info&&!source&&!info.can_train&&!model);
  // The page that led here (the checked-sounds list) can be gone back to; this page passes itself on to the search.
  const origin=returnPath(page.url,/^\/datasets\/\d+\/heard(\?[^#]*)?$/),here=`/datasets/${id}/species/${speciesId}/model?${range}${origin?`&return=${encodeURIComponent(origin)}`:''}`;
  const searchHref=`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}&return=${encodeURIComponent(here)}`;
  const back=$derived(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}${model?.applicable?`&engine=custom&model_id=${model.id}`:''}`);
  const time=(row:{local_time:string})=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:site?.timezone}).format(new Date(row.local_time));
  const made=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',hourCycle:'h23',timeZone:site?.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
  const score=(value:number)=>{const text=value.toFixed(2).replace('-0.00','0.00');
    return source==='custom'?fill('mdScoreCustom',{value:text}):source==='search'?fill('pSimilarity',{value:text}):fill('mdScoreBuiltin',{model:modelName(info!.engine),value:text});};
</script>
<svelte:window onkeydown={shortcut}/>
<div class="page">
  <div class="crumbs">{#if origin}<a href={appHref(origin)}>{t('wReturn')}</a>{/if}<a href={back}>{t('wBackSpecies')}</a></div>
  <div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{info?fill('customModelTitleFor',{name:commonName(info.species,app.locale)||info.species.scientific_name}):t('customModelTitle')}</h1></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if info}
    <ol class="steps" aria-label={t('tpSteps')}>{#each ['tpStep1','tpStep2','tpStep3'] as name,i}{@const stage=collecting?0:measured||model?.growth.state==='settled'?2:1}
      <li aria-current={i===stage?'step':undefined}>{t(name)}{i<stage||i===2&&measured?.stage==='complete'?' ✓':''}</li>{/each}</ol>
    {#if newer}{@const name=versionName(newer)}<section class="panel newer" aria-labelledby="newer-title"><h2 id="newer-title">{fill('vnNewTitle',{site:newer.site??''})}</h2>
      <p>{fill('vnNewText',{version:versionFull(newer),positive:newer.training.positive_5s,negative:newer.training.negative_5s})}{#if newer.round>1}{' '}{fill('vnLatest',{site:newer.site??'',n:newer.round})}{/if}</p>
      {#if newer.tests}{@const tests=newer.tests}<h3>{t('vnCmpTitle')}</h3>
        <div class="scroll"><table><thead><tr><th></th><th scope="col">{t('vnNow')}<br>{nowName}</th><th scope="col">{name}</th></tr></thead><tbody>
          <tr><th scope="row">{t('tsFoundLabel')}</th><td class="big">{tests.now.found} / {tests.heard}</td><td class="big" class:good={tests.new.found>tests.now.found} class:bad={tests.new.found<tests.now.found}>{tests.new.found} / {tests.heard}</td></tr>
          <tr><th scope="row">{t('tsFalseLabel')}</th><td class="big">{tests.now.false} / {tests.not_heard}</td><td class="big" class:good={tests.new.false<tests.now.false} class:bad={tests.new.false>tests.now.false}>{tests.new.false} / {tests.not_heard}</td></tr>
        </tbody></table></div>
        <p class="muted">{fill('vnSmall',{heard:tests.heard,not_heard:tests.not_heard})}</p>
        {#if newer.differ.length}<details><summary>{fill('vnDiffer',{n:newer.differ.length})}</summary>{#each newer.differ as sound (sound.segment_id)}<div class="sound">
          <span>{time(sound)}　{fill('vnDifferRow',{now:said(sound.heard,sound.now),version:name,new:said(sound.heard,sound.new)})}</span>
          <AudioRegion source={{id:sound.recording_id,kind:'recording',path:sound.path,seconds:sound.seconds}} fixedRange compact start={sound.start_s} end={sound.end_s}/></div>{/each}</details>{/if}
      {:else}<p class="muted">{t('vnNoTests')}</p>{/if}
      <div class="actions"><Button variant="primary" pending={switchTo.pending} disabled={keepNow.pending} onclick={()=>switchTo.run(newer!.id)}>{fill('vnSwitch',{version:name})}</Button>
        <Button pending={keepNow.pending} disabled={switchTo.pending} onclick={()=>keepNow.run(newer!.id)}>{fill('vnKeep',{version:nowName})}</Button></div>
      <p class="muted">{t('vnAfter')}</p>
    </section>{/if}
    {#if collecting&&!model}<BorrowAI datasetId={id} {speciesId} speciesName={commonName(info.species,app.locale)||info.species.scientific_name} range={range.toString()} ontaken={async()=>{skipped=[];await next.run();}}/>{/if}
    {#if collecting}{@const test=info.test}{@const few=test.heard<test.aim_heard}<section class="panel"><h2 id="test-title">{t('tpTitle')}</h2>
      {#if model?.imported}<p class="info">{fill('vbTrying',{version:nowName})}</p>{/if}
      <p>{t('tpIntro')}</p>
      <div class="meter">{#each [['mdVoice',test.heard,test.aim_heard],['mdNoVoice',test.not_heard,test.aim_not_heard]] as [name,n,of]}
        <span>{t(name as string)}</span><progress max={of as number} value={Math.min(n as number,of as number)} aria-label={t(name as string)}></progress><span>{fill('tpCount',{n,of})}</span>{/each}</div>
      <p class="muted">{t('tpCountNote')}</p>
      <p class="muted">{t('tpWhy')}</p>
      {#if model?.imported&&source==='custom'&&!test.complete}<p class="muted">{fill('vbLayers',{version:nowName})}</p>{:else if chosen&&!test.complete}<p class="muted">{chosen}</p>{/if}
      {#if test.complete}<div class="status-box" role="status"><strong>{t('tpReadyTitle')}</strong><p>{fill('tpReadyText',{heard:test.heard,not_heard:test.not_heard})}</p></div>
        <div class="actions"><Button variant="primary" pending={startGrowing.pending} onclick={()=>startGrowing.run()}>{t('tpStart')}</Button></div>
      <!-- Many answers and still short, or nothing left to offer: growing can start with the test sounds found so far. -->
      {:else if test.answered>=test.ask_after||(!next.pending&&!batch.length&&!!source)}
        {#if test.answered}<div class="status-box" role="status"><strong>{fill(few?'tpShortHeardTitle':'tpShortNotHeardTitle',{n:few?test.heard:test.not_heard})}</strong>
          <p>{fill(few?'tpShortHeardText':'tpShortNotHeardText',{answered:test.answered,n:few?test.heard:test.not_heard,aim:few?test.aim_heard:test.aim_not_heard})}</p></div>{/if}
        {#if test.heard>=test.enough}<div class="actions"><Button variant="primary" pending={startGrowing.pending} onclick={()=>startGrowing.run()}>{t('tpEarly')}</Button></div>
          <p class="muted">{fill('tpEarlyNote',{heard:test.heard,not_heard:test.not_heard})}</p>
        {:else}<p class="info">{fill('tpStuck',{n:test.enough})}</p>{/if}{/if}
    </section>{/if}
    {#snippet counts()}<p><strong>{fill('mdCounts',{positive:info!.examples.positive_5s,negative:info!.examples.negative_5s})}</strong>
      {#if info!.needed}<span class="muted">{fill('mdNeed',{n:info!.needed,each:info!.requirements.positive_5s})}</span>{/if}</p>
      {#if info!.examples.other_answers}<p class="muted">{fill('mdElsewhere',{n:info!.examples.other_answers})}</p>{/if}
      {#if info!.examples.no_bird_answers}<p class="muted">{fill('mdNoBird',{n:info!.examples.no_bird_answers})}</p>{/if}
      {#if info!.test&&info!.test.heard+info!.test.not_heard}<p class="muted">{fill('tsMaterialNote',{n:info!.test.heard+info!.test.not_heard})}</p>{/if}{/snippet}
    {#if !searchFirst&&!collecting}<section class="panel">
      {@render counts()}
      {#if info.phase==='fixed'}<p class="muted">{t('tpGrowNote')}</p>{/if}
      {#if model?.imported}<p class="info">{#if model.imported.file}{fill(model.imported.site?'shImportedFrom':'shImported',{site:model.imported.site??'',file:model.imported.file,date:made(model.created_at)})}{:else}{fill('vbBorrowed',{version:nowName,date:made(model.created_at)})}{/if}{#if model.imported.warning}{' '}{fill('shWarnText',{from:modelName(model.imported.engine),here:modelName(info.engine)})}{/if} {t('vbBack')}</p>
      {:else}<p>{model?fill('mdRound',{n:model.round,date:made(model.created_at)}):t('mdNoAI')}{#if model?.parent}{' '}{fill('vbGrewFrom',{version:versionName(model.parent)})}{/if}</p>{/if}
      {#if info.inherited}<p class="muted">{fill('vbInherited',{n:info.inherited.positive_5s+info.inherited.negative_5s,sites:info.inherited.sites.join('・')||'—'})}</p>{/if}
      {#if model}<p><a href={listHref}>{t('vlLink')}</a></p>{/if}
      {#if model&&!model.imported&&model.training.other_sites}<p class="muted">{fill('shPooled',{n:model.training.other_sites.positive_5s+model.training.other_sites.negative_5s,sites:model.training.other_sites.sites})}</p>{/if}
      {#if model}<p class="muted">{t('mdStopRule')}</p>{/if}
      {#if model&&!model.applicable}<p class="info">{t(model.reason??'custom_model_scope_changed')}</p>{/if}
      <!-- Shown from the first time the AI is made again: before that there is nothing to compare it with. -->
      {#if model?.growth.rounds.length}{@const grown=model.growth.state}<h2>{t('mdGrowth')}</h2>
        <ul class="rounds">{#each model.growth.rounds as item (item.round)}<li>{item.overlap==null?fill('mdGrowthRoundNone',{n:item.round}):fill('mdGrowthRound',{n:item.round,p:share(item.overlap)})}</li>{/each}</ul>
        {#if model.growth.rounds.some(item=>item.overlap!=null)}<p class="muted">{t('mdGrowthNote')}</p>{/if}
        <div class="status-box" role="status"><strong>{t(`mdGrow_${growText}`)}</strong><p>{fill(`mdGrowText_${growText}`,{n:info.examples.positive_5s})}</p></div>
        <!-- Once settled, the sounds below wait behind "Grow it a little more"; step 3 follows the scores. -->
        {#if grown!=='growing'}<div class="actions">
          {#if grown!=='settled'}<Button href={back}>{t('mdFinish')}</Button>{:else if !more}<Button onclick={async()=>{more=true;await tick();toTop();}}>{t('mdGrowMore')}</Button>{/if}
        </div>{/if}
      {/if}
    </section>
    <TestSounds datasetId={id} {speciesId} query={range.toString()} timezone={site?.timezone}
      version={`${model?.id}:${model?.round}:${info.examples.positive_5s}:${info.examples.negative_5s}:${info.test?.heard}:${info.test?.not_heard}`}
      check={model?info.check:null} oncontinue={()=>startCheck.run()} reveal={returned}
      title={model?.imported?fill('vbScoreTitle',{version:nowName}):''} onreport={value=>report=value}/>
    <!-- An AI brought from elsewhere, now measured here: grow it further with this site's sounds, or use it as it is. -->
    {#if model?.imported&&info.phase==='fixed'}{@const there=model.imported.score}{@const row=report?.state==='ready'?report.rounds.find(item=>item.current&&!item.made_from_test):null}
      {@const own=report?.state==='ready'?report.sounds:null}{@const origin=model.imported.site??nowName}
      <section class="panel"><h2>{t('vbNextTitle')}</h2>
        {#if there&&model.imported.site}<p class="muted">{fill('vbThereWas',{site:model.imported.site,...there})}</p>
          {#if row&&own&&own.heard&&own.not_heard&&there.heard&&there.not_heard}<p>{fill('vbCmp',{site:model.imported.site,miss:compare(1-(row.found??0)/own.heard,1-there.found/there.heard),false:compare((row.false??0)/own.not_heard,there.false/there.not_heard)})}</p>{/if}{/if}
        <div class="choice"><strong>{t('vbGrowTitle')}</strong>{t('vbRecommended')}
          <p>{info.inherited?fill('vbGrowText',{n:info.inherited.positive_5s+info.inherited.negative_5s,sites:info.inherited.sites.join('・')||origin,here:site?.name??'',version:nowName,origin}):fill('vbGrowTextNone',{here:site?.name??'',version:nowName,origin})}</p>
          <Button variant="primary" onclick={toTop}>{t('tpStart')}</Button></div>
        <div class="choice"><strong>{t('vbKeepTitle')}</strong><p>{fill('vbKeepText',{version:nowName})}</p>
          <Button pending={startCheck.pending} onclick={()=>startCheck.run()}>{t('vbKeep')}</Button></div>
      </section>{/if}
    <!-- Step 3, offered once an AI exists: the same check as "Check the AI's answers", told as adding test sounds. -->
    {#if model?.applicable}<section class="panel"><h2>{t('tpStep3')}</h2>
      <p>{t('s3Intro')}</p><p>{t('s3How')} {t('s3Material')}</p>
      {#if measured}<div class="actions"><Button variant={measured.stage==='complete'?'default':'primary'} pending={startCheck.pending} onclick={()=>startCheck.run()}>{listenLabel(measured)}</Button></div>
      {:else}<p class="info">{t(info.check?.status==='stale'?'s3Again':'s3First')}</p>
        <div class="actions"><Button variant={model.growth.state==='settled'?'primary':'default'} disabled={asking} onclick={()=>asking=true}>{t('s3Start')}</Button></div>
        {#if asking}<div class="confirm"><p>{t('s3Confirm')}</p><div class="actions"><Button disabled={startCheck.pending} onclick={()=>asking=false}>{t('s3Stop')}</Button>
          <Button variant="primary" pending={startCheck.pending} onclick={()=>startCheck.run()}>{t('s3Go')}</Button></div></div>{/if}{/if}
    </section>{/if}{/if}
    <!-- `keys`: a row of the next sounds, which can be the selected one; pressing or focusing anything in it selects it. -->
    {#snippet row(item:LabelRow,keys=false)}{@const on=keys&&item===current}<article class="row" class:on aria-current={on?'true':undefined}
      onpointerdown={keys?()=>selected=item.segment_id:undefined} onfocusin={keys?()=>selected=item.segment_id:undefined}>
      <div class="row-head"><span>{time(item)}</span>{#if item.test&&!collecting}<span class="test">{t('tsBadgeHere')}</span>{/if}{#if item.score!=null}<span class="muted">{score(item.score)}</span>{/if}</div>
      <AudioRegion source={{id:item.recording_id,kind:'recording',path:item.path,seconds:item.seconds}} fixedRange compact start={item.start_s} end={item.end_s}>
        {#snippet actions()}<div class="actions" role="group" aria-label={time(item)}>{#each [['target','mdVoice'],['other','mdNoVoice']] as [value,name],i}
          <Button active={item.label===value} aria-pressed={item.label===value} aria-keyshortcuts={on?String(i+1):undefined} pending={label.pending} disabled={next.pending||build.pending} onclick={()=>label.run(item,value as 'target'|'other')}>{item.label===value?'✓ ':''}{t(name)}{#if on} <kbd>{i+1}</kbd>{/if}</Button>{/each}
          <Button active={unsure.includes(item.segment_id)} aria-pressed={unsure.includes(item.segment_id)} aria-keyshortcuts={on?'3':undefined} pending={label.pending} disabled={next.pending||build.pending} onclick={()=>notSure(item)}>{unsure.includes(item.segment_id)?'✓ ':''}{t('mdUnsure')}{#if on} <kbd>3</kbd>{/if}</Button></div>{/snippet}
      </AudioRegion>
      <OtherSpecies datasetId={id} of={speciesId} recordingId={item.recording_id} start={item.start_s} end={item.end_s} screen="training"/>
    </article>{/snippet}
    {#if listed}<section class="panel"><h2 id="next-sounds">{t('mdNextTitle')}</h2>
      <!-- Nothing to rank by is a step still to do, not an error. -->
      {#if !source}<p class="info" role="status">{t('custom_search_needed')}</p>
        <!-- Sounds already checked by ear can be the examples: the search is made here and the sounds to answer appear. -->
        {#if site}<HeardBox datasetId={id} {speciesId} timezone={site.timezone} hasSearch={false} range={range.toString()} searchHref="" resultsHref="" ondone={()=>next.run()}/>{/if}
        <Button href={searchHref}>{t('sSearchStart')}</Button>
      {:else}
        {#if !model&&info.needed&&!collecting}<p>{fill('mdFirst',{n:info.requirements.total_5s})}</p>{/if}
        <p>{t('mdShortIntro')}</p>
        <!-- The details stay one click away so the first sound is in view. -->
        <details><summary>{t('mdHowChosen')}</summary>
          {#if !collecting}<p>{chosen}</p>{/if}
          <p class="muted">{t('mdUndo')}</p><p class="muted">{t('mdUnsureHint')}</p><p class="muted">{t('mdEvidence')}</p></details>
        {#if batch.length}<p class="muted">{t('mdKeys')}</p>{/if}
        {#each batch as item (item.segment_id)}{@render row(item,true)}{:else}
          {#if collecting}<p>{t('mdNoneLeft')}</p>
          {:else}<div class="status-box" role="status"><strong>{t('mdEndTitle')}</strong><p>{t('mdEndText')}</p></div><p class="muted">{t('mdEndMore')}</p>{/if}{/each}
      {/if}
      <!-- While the test sounds are made, the AI cannot be made yet: no counts and no button for it. -->
      {#if !searchFirst||collecting}<div class="footer">
        {#if !collecting}{@render counts()}
          {#each info.reasons.filter(reason=>reason!=='custom_more_answers_needed') as reason}<p class="info">{t(reason)}</p>{/each}
          {#if measured&&info.can_train}<p class="info">{t('s3RebuildNote')}</p>{/if}{/if}
        <div class="actions">
          {#if !collecting}<Button variant="primary" disabled={!info.can_train} pending={build.pending} onclick={()=>build.run()}>{t(model?'mdRebuild':'customModelCreate')}</Button>{/if}
          {#if source}<Button disabled={build.pending} pending={next.pending} onclick={another}>{t('mdAnother')}</Button>{/if}
          <Button href={back}>{t('mdDone')}</Button>
        </div>
        {#if build.pending}<p role="status"><Spinner/> {t('customModelPreparing')}</p><Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>{/if}
        {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
        {#if next.error&&!error}<p class="error" role="alert">{t(next.error)}</p>{/if}
      </div>{/if}
    </section>{/if}
    <!-- While growing (also once it has settled): a call the AI was not given an example of can be added; the search page keeps the current examples. -->
    {#if source&&!collecting}<section class="panel"><h2>{t('mdMoreCallsTitle')}</h2><p>{t('mdMoreCallsText')}</p>{#if info.search_examples}<p class:info={added} role={added?'status':undefined}>{fill(added?'mdExamplesAdded':'mdExamplesNow',{n:info.search_examples})}</p>{/if}<p class="muted">{t('mdMoreCallsNote')}</p>
      <div class="actions">{#if added&&listed}<Button variant="primary" onclick={toTop}>{t('mdSeeNext')}</Button>{/if}<Button href={searchHref}>{t('mdMoreCallsAction')}</Button></div></section>{/if}
    <!-- The count follows each answer and undo; new answers are added on top, and the list is reordered only with a new batch. -->
    {#if answered.length}<details class="panel"><summary>{fill('mdAnswered',{n:info.answered.length})}</summary>
      <p class="muted">{t('mdUndo')}</p>
      {#each answered.slice(0,answeredShown) as item (item.segment_id)}{@render row(item)}{/each}
      {#if answeredShown<answered.length}<Button onclick={()=>answeredShown+=10}>{t('pMore')}</Button>{/if}
    </details>{/if}
    {#if (!searchFirst||model)&&!collecting}<AiSharing datasetId={id} {speciesId} {info} onchange={async()=>{skipped=[];await next.run();}}/>{/if}
    {#if model}<details class="more"><summary>{t('sMore')}</summary>
      <Button variant="danger" size="sm" onclick={()=>confirming=!confirming}>{t(model.imported?'shDeleteImported':'mdDeleteAI')}</Button>
      {#if confirming}<div class="confirm"><p>{t(model.imported?'shDeleteImportedConfirm':'modelDeleteConfirm')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>confirming=false}>{t('mCancel')}</Button>
        <Button variant="danger" pending={remove.pending} onclick={()=>remove.run(model.id)}>{t('modelDeleteYes')}</Button></div></div>{/if}
    </details>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.choice{margin:0 0 12px;padding:14px 18px;border:1px solid rgb(var(--stone-300));border-radius:12px}.choice p{margin:8px 0}
  .newer{border-color:rgb(var(--primary-700))}.newer table{border-collapse:collapse}.newer th,.newer td{padding:8px 12px;border-bottom:1px solid rgb(var(--stone-300));text-align:left;font-weight:400}
  .newer thead th{font-weight:600}.newer td.big{font-size:22px;font-weight:700}.good{color:rgb(var(--color-success))}.bad{color:rgb(var(--color-danger))}.scroll{overflow-x:auto}
  .newer details summary{min-height:44px;padding:10px 0;cursor:pointer}.newer .sound{display:flex;flex-direction:column;gap:6px;padding:12px 0;border-top:1px solid rgb(var(--stone-300))}
  .steps{display:flex;flex-direction:row;flex-wrap:wrap;gap:8px;margin:0;padding:0;list-style:none}
  .steps li{padding:4px 14px;border:1px solid rgb(var(--stone-300));border-radius:999px;font-size:14px}.steps li[aria-current]{border-color:rgb(var(--primary-700));color:rgb(var(--primary-700));font-weight:600}
  .meter{display:grid;grid-template-columns:auto minmax(0,320px) auto;gap:4px 12px;align-items:center;justify-content:start}.meter progress{width:100%;height:12px}
  .test{padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;color:rgb(var(--primary-700));font-size:13px;font-weight:600}
  section p{margin:12px 0}.rounds{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;padding:0;list-style:none}
  .rounds li{padding:4px 10px;border:1px solid rgb(var(--stone-300));border-radius:8px;font-size:14px}.status-box{margin-bottom:12px}.row{display:flex;flex-direction:column;gap:8px;padding:16px 12px;border-top:1px solid rgb(var(--stone-300));border-left:4px solid transparent;scroll-margin-block:12px}
  .row.on{border-left-color:rgb(var(--primary-700));background:rgb(var(--primary-700) / .07)}
  kbd{margin-left:6px;padding:0 6px;border:1px solid currentColor;border-radius:4px;font:inherit;font-size:13px;opacity:.75}
  .row-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}.footer{border-top:1px solid rgb(var(--stone-300));padding-top:4px}
  details summary{min-height:44px;padding:10px 0;cursor:pointer}.confirm{margin-top:8px;padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}</style>
