<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount,tick } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,siteOf } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { fill,modelIntro,modelName,share,type LabelRow,type SpeciesAI,type Validation } from '$lib/workflow';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import OtherSpecies from '$lib/OtherSpecies.svelte';
  import TestSounds from '$lib/TestSounds.svelte';
  import AiSharing from '$lib/AiSharing.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { notify } from '$lib/ui/toast.svelte';
  const id=Number(page.params.id),speciesId=page.params.species!;
  const site=$derived(siteOf(datasetOf(id)));
  const range=new URLSearchParams([...new URL(page.url.hash.slice(1),page.url).searchParams].filter(([key])=>key.endsWith('_date')));
  // `info` holds the counts and the AI; the batch changes only when a new batch is loaded, so its rows do not move while answering.
  let info=$state<SpeciesAI|null>(null),batch=$state<LabelRow[]>([]),answered=$state<LabelRow[]>([]),source=$state<SpeciesAI['source']>(null);
  let error=$state(''),skipped:number[]=[],answeredShown=$state(10),confirming=$state(false);
  // A sound is answered only after it was played, as on the other answer pages; `more` opens the sounds again once growing has settled.
  let played=$state<number[]>([]),more=$state(false);
  // Sounds answered "Not sure": left without an answer, so they are never material for the AI and the next batch skips them.
  let unsure=$state<number[]>([]);
  const notSure=async(row:LabelRow)=>{if(row.label)await label.run(row,row.label);if(row.label)return;const on=!unsure.includes(row.segment_id);
    // "Not sure" is kept as a record too, so it shows among the sounds checked by ear; it is no material for the AI.
    try{await api(`/datasets/${id}/species/${speciesId}/labels`,{segment_id:row.segment_id,label:on?'unknown':null});}catch{return;}
    unsure=on?[...unsure,row.segment_id]:unsure.filter(id=>id!==row.segment_id);};
  const fetchState=(query='')=>api<SpeciesAI>(`/datasets/${id}/species/${speciesId}/custom-model?${range}${query}`);
  // Sounds shown and passed over are not offered again until the AI is made again.
  const next=createTask(async()=>{const value=await fetchState(`&batch=true${skipped.map(segment=>`&skip=${segment}`).join('')}`);
    info=value;batch=value.batch??[];answered=value.answered;source=value.source;answeredShown=10;});
  onMount(async()=>{await next.run();if(next.error)error=next.error;});
  const label=createTask(async(row:LabelRow,value:'target'|'other')=>{
    const saved=await api<{label:LabelRow['label']}>(`/datasets/${id}/species/${speciesId}/labels`,{segment_id:row.segment_id,label:row.label===value?null:value});
    row.label=saved.label;if(saved.label)unsure=unsure.filter(id=>id!==row.segment_id);info=await fetchState();
    // A sound answered in this batch joins the answered list at once, as the same row, so both show the same answer.
    if(saved.label&&!answered.includes(row))answered.unshift(row);
  },{toast:true});
  // The period the server used for this page, so a link without dates still makes the AI for what is shown.
  const build=createTask(async()=>{await api('/custom-models',{dataset_id:id,species_id:speciesId,
    start_date:info!.start_date,end_date:info!.end_date});skipped=[];await next.run();toTop();
    notify('mdBuilt','success',undefined,{n:info?.model?.round??1});});
  // A new batch is answered from its first sound, so bring the top of the list back into view.
  const toTop=()=>document.getElementById('next-sounds')?.scrollIntoView({block:'start'});
  const another=async()=>{skipped.push(...batch.filter(row=>!row.label).map(row=>row.segment_id));await next.run();toTop();};
  const stopOperation=createTask(async()=>{await api('/operations/cancel',{});});
  const remove=createTask(async(modelId:number)=>{await api(`/custom-models/${modelId}/delete`,{});confirming=false;skipped=[];await next.run();},{toast:true});
  const model=$derived(info?.model??null);
  // The words for how far it has grown: one round that hardly changed it (growth_state's 0.9) is "almost", not "changed".
  const growText=$derived(model?.growth.state==='growing'&&(model.growth.rounds.at(-1)?.overlap??0)>=.9?'almost':model?.growth.state);
  const folded=$derived(model?.growth.state==='settled'&&!more);
  // Opens this AI's accuracy check at once (an unfinished one with the same AI and period is reused).
  const startCheck=createTask(async()=>{const validation=await api<Validation>('/validations',{dataset_id:id,species_id:speciesId,
    start_date:info!.start_date,end_date:info!.end_date,model:'custom',model_id:model!.id});await goto(resolve(`/datasets/${id}/validation/${validation.id}`));},{toast:true});
  // Nothing to rank by and nothing to make an AI from yet: the page only points to the search.
  const searchFirst=$derived(!!info&&!source&&!info.can_train&&!model);
  const back=$derived(`${resolve(`/datasets/${id}/species/${speciesId}`)}?${range}${model?.applicable?`&engine=custom&model_id=${model.id}`:''}`);
  const time=(row:LabelRow)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'medium',hourCycle:'h23',timeZone:site?.timezone}).format(new Date(row.local_time));
  const made=(value:string)=>new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',hourCycle:'h23',timeZone:site?.timezone}).format(new Date(`${value.replace(' ','T')}Z`));
  const score=(value:number)=>{const text=value.toFixed(2);
    return source==='custom'?fill('mdScoreCustom',{value:text}):source==='search'?fill('pSimilarity',{value:text}):fill('mdScoreBuiltin',{model:modelName(info!.engine),value:text});};
</script>
<div class="page">
  <div class="crumbs"><a href={back}>{t('wBackSpecies')}</a></div>
  <div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{info?fill('customModelTitleFor',{name:commonName(info.species,app.locale)||info.species.scientific_name}):t('customModelTitle')}</h1></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if info}
    {#snippet counts()}<p><strong>{fill('mdCounts',{positive:info!.examples.positive_5s,negative:info!.examples.negative_5s})}</strong>
      {#if info!.needed}<span class="muted">{fill('mdNeed',{n:info!.needed,each:info!.requirements.positive_5s})}</span>{/if}</p>
      {#if info!.examples.other_answers}<p class="muted">{fill('mdElsewhere',{n:info!.examples.other_answers})}</p>{/if}
      {#if info!.test&&info!.test.heard+info!.test.not_heard}<p class="muted">{fill('tsMaterialNote',{n:info!.test.heard+info!.test.not_heard})}</p>{/if}{/snippet}
    {#if !searchFirst}<section class="panel">
      {@render counts()}
      {#if model?.imported}<p class="info">{fill(model.imported.site?'shImportedFrom':'shImported',{site:model.imported.site??'',file:model.imported.file,date:made(model.created_at)})}{#if model.imported.warning}{' '}{fill('shWarnText',{from:modelName(model.imported.engine),here:modelName(info.engine)})}{/if} {t('shImportedBack')}</p>
      {:else}<p>{model?fill('mdRound',{n:model.round,date:made(model.created_at)}):t('mdNoAI')}</p>{/if}
      {#if model&&!model.imported&&model.training.other_sites}<p class="muted">{fill('shPooled',{n:model.training.other_sites.positive_5s+model.training.other_sites.negative_5s,sites:model.training.other_sites.sites})}</p>{/if}
      {#if model}<p class="muted">{t('mdStopRule')}</p>{/if}
      {#if model&&!model.applicable}<p class="info">{t(model.reason??'custom_model_scope_changed')}</p>{/if}
      <!-- Shown from the first time the AI is made again: before that there is nothing to compare it with. -->
      {#if model?.growth.rounds.length}{@const grown=model.growth.state}<h2>{t('mdGrowth')}</h2>
        <ul class="rounds">{#each model.growth.rounds as item (item.round)}<li>{item.overlap==null?fill('mdGrowthRoundNone',{n:item.round}):fill('mdGrowthRound',{n:item.round,p:share(item.overlap)})}</li>{/each}</ul>
        {#if model.growth.rounds.some(item=>item.overlap!=null)}<p class="muted">{t('mdGrowthNote')}</p>{/if}
        <div class="status-box" role="status"><strong>{t(`mdGrow_${growText}`)}</strong><p>{fill(`mdGrowText_${growText}`,{n:info.examples.positive_5s})}</p></div>
        <!-- The check opens directly; finishing returns to the species page. Once settled, the sounds below wait behind "Grow it a little more". -->
        {#if grown!=='growing'}<div class="actions">
          {#if grown!=='no_voice'&&model.applicable}<Button variant="primary" pending={startCheck.pending} onclick={()=>startCheck.run()}>{t('cStart')}</Button>{/if}
          {#if grown!=='settled'}<Button href={back}>{t('mdFinish')}</Button>{:else if !more}<Button onclick={async()=>{more=true;await tick();toTop();}}>{t('mdGrowMore')}</Button>{/if}
        </div>{/if}
      {/if}
    </section>
    <TestSounds datasetId={id} {speciesId} name={commonName(info.species,app.locale)||info.species.scientific_name} query={range.toString()} timezone={site?.timezone}
      version={`${model?.id}:${model?.round}:${info.examples.positive_5s}:${info.examples.negative_5s}:${info.test?.heard}:${info.test?.not_heard}`}/>{/if}
    {#snippet row(item:LabelRow)}<article class="row">
      <div class="row-head"><span>{time(item)}</span>{#if item.test}<span class="test">{t('tsBadgeHere')}</span>{/if}{#if item.score!=null}<span class="muted">{score(item.score)}</span>{/if}</div>
      <AudioRegion source={{id:item.recording_id,kind:'recording',path:item.path,seconds:item.seconds}} fixedRange compact start={item.start_s} end={item.end_s}
        onplaying={()=>{if(!played.includes(item.segment_id))played=[...played,item.segment_id];}}>
        {#snippet actions()}<div class="actions" role="group" aria-label={time(item)}>{#each [['target','mdVoice'],['other','mdNoVoice']] as [value,name]}
          <Button active={item.label===value} aria-pressed={item.label===value} pending={label.pending} disabled={next.pending||build.pending||(!item.label&&!played.includes(item.segment_id))} onclick={()=>label.run(item,value as 'target'|'other')}>{item.label===value?'✓ ':''}{t(name)}</Button>{/each}
          <Button active={unsure.includes(item.segment_id)} aria-pressed={unsure.includes(item.segment_id)} pending={label.pending} disabled={next.pending||build.pending||(!item.label&&!played.includes(item.segment_id))} onclick={()=>notSure(item)}>{unsure.includes(item.segment_id)?'✓ ':''}{t('mdUnsure')}</Button>
          {#if !item.label&&!played.includes(item.segment_id)}<span class="muted">{t('pPlayFirst')}</span>{/if}</div>{/snippet}
      </AudioRegion>
      <OtherSpecies datasetId={id} of={speciesId} recordingId={item.recording_id} start={item.start_s} end={item.end_s} screen="training" disabled={!item.label&&!played.includes(item.segment_id)}/>
    </article>{/snippet}
    {#if !folded}<section class="panel"><h2 id="next-sounds">{t('mdNextTitle')}</h2>
      <!-- Nothing to rank by is a step still to do, not an error. -->
      {#if !source}<p class="info" role="status">{t('custom_search_needed')}</p>
        <Button href={`${resolve(`/datasets/${id}/search`)}?species=${speciesId}&${range}`}>{t('sSearchStart')}</Button>
      {:else}
        {#if !model&&info.needed}<p>{fill('mdFirst',{n:info.requirements.total_5s})}</p>{/if}
        <p>{t('mdShortIntro')}</p>
        <!-- The details stay one click away so the first sound is in view. -->
        <details><summary>{t('mdHowChosen')}</summary>
          <p>{source==='custom'?t('mdIntroCustom'):source==='search'?t('mdIntroSearch'):fill('mdIntroBuiltin',{model:modelIntro(info.engine)})}</p>
          <p class="muted">{t('mdUndo')}</p><p class="muted">{t('mdUnsureHint')}</p><p class="muted">{t('mdEvidence')}</p></details>
        {#each batch as item (item.segment_id)}{@render row(item)}{:else}<p>{t('mdNoneLeft')}</p>{/each}
      {/if}
      {#if !searchFirst}<div class="footer">
        {@render counts()}
        {#each info.reasons.filter(reason=>reason!=='custom_more_answers_needed') as reason}<p class="info">{t(reason)}</p>{/each}
        <div class="actions">
          <Button variant="primary" disabled={!info.can_train} pending={build.pending} onclick={()=>build.run()}>{t(model?'mdRebuild':'customModelCreate')}</Button>
          {#if source}<Button disabled={build.pending} pending={next.pending} onclick={another}>{t('mdAnother')}</Button>{/if}
          <Button href={back}>{t('mdDone')}</Button>
        </div>
        {#if build.pending}<p role="status"><Spinner/> {t('customModelPreparing')}</p><Button pending={stopOperation.pending} onclick={()=>stopOperation.run()}>{t('operationCancel')}</Button>{/if}
        {#if build.error}<p class="error" role="alert">{t(build.error)}</p>{/if}
        {#if next.error&&!error}<p class="error" role="alert">{t(next.error)}</p>{/if}
      </div>{/if}
    </section>{/if}
    <!-- The count follows each answer and undo; new answers are added on top, and the list is reordered only with a new batch. -->
    {#if answered.length}<details class="panel"><summary>{fill('mdAnswered',{n:info.answered.length})}</summary>
      <p class="muted">{t('mdUndo')}</p>
      {#each answered.slice(0,answeredShown) as item (item.segment_id)}{@render row(item)}{/each}
      {#if answeredShown<answered.length}<Button onclick={()=>answeredShown+=10}>{t('pMore')}</Button>{/if}
    </details>{/if}
    {#if !searchFirst||model}<AiSharing datasetId={id} {speciesId} {info} onchange={async()=>{skipped=[];await next.run();}}/>{/if}
    {#if model}<details class="more"><summary>{t('sMore')}</summary>
      <Button variant="danger" size="sm" onclick={()=>confirming=!confirming}>{t(model.imported?'shDeleteImported':'mdDeleteAI')}</Button>
      {#if confirming}<div class="confirm"><p>{t(model.imported?'shDeleteImportedConfirm':'modelDeleteConfirm')}</p><div class="actions"><Button disabled={remove.pending} onclick={()=>confirming=false}>{t('mCancel')}</Button>
        <Button variant="danger" pending={remove.pending} onclick={()=>remove.run(model.id)}>{t('modelDeleteYes')}</Button></div></div>{/if}
    </details>{/if}
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.test{padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;color:rgb(var(--primary-700));font-size:13px;font-weight:600}
  section p{margin:12px 0}.rounds{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0;padding:0;list-style:none}
  .rounds li{padding:4px 10px;border:1px solid rgb(var(--stone-300));border-radius:8px;font-size:14px}.status-box{margin-bottom:12px}.row{display:flex;flex-direction:column;gap:8px;padding:16px 0;border-top:1px solid rgb(var(--stone-300))}
  .row-head{display:flex;flex-wrap:wrap;gap:4px 16px;align-items:baseline}.footer{border-top:1px solid rgb(var(--stone-300));padding-top:4px}
  details summary{min-height:44px;padding:10px 0;cursor:pointer}.confirm{margin-top:8px;padding:12px;border:1px solid rgb(var(--color-danger) / .5);border-radius:8px}</style>
