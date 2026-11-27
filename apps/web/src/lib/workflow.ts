import { resolve } from '$app/paths';
import { tick } from 'svelte';
import type { Species } from './species';
import type { Report, ReportOptions } from './reports';
import type { AudioSource } from './AudioRegion.svelte';
import { app,t,duration } from './app.svelte';
export type Metric = { value: number | null; low: number | null; high: number | null };
export type CheckStatus = 'not_started'|'in_progress'|'decided'|'undecided'|'stale';
/** One check's estimates with 90% ranges; present once every sound is answered, or when the first block found no call (`early_stop`, without density or count). */
export type QualityResult = { status:'decided'|'undecided'; threshold:number|null; reason:string|null; density:Metric|null; count:Metric|null;
  precision:Metric|null; recall:Metric|null; recall_status:'estimated'|'not_estimable'|'not_estimable_rare'; auprc:Metric|null;
  unknown:number; disagreement:boolean; answers_per_bin:{heard:number;not_heard:number;unknown:number}[] };
/** One model's check for one species: up to 60 sounds, the two top score groups first (v2); older checks mixed them and could add 18 more. */
export type Validation = { id:number; dataset_id:number; species:Species; stage:'sample'|'extension'|'complete'; status:CheckStatus;
  model:string; engine:string; model_id:number|null; results:Record<string,QualityResult>;
  counts:{total:number;answered:number;unknown:number;bins:{total:number;heard:number}[]}; method:string; early_stop:boolean;
  question:{id:number;position:number;verdict:string|null;version:number;source:AudioSource;start_s:number;end_s:number;review_position:number;review_total:number}|null;
  history:{id:number;position:number;verdict:string}[]; can_extend:boolean; extended:boolean; model_reason?:string|null;
  start_date:string; end_date:string; window_s:number; population:number; applicable:boolean; created_at:string };
/** How far a species AI has grown, and one record per time it was made again: `overlap` is the share of sounds scored 0.5 or more that
 * stayed the same (null when either AI had none); `top_voice` counts voice among the two highest-scoring new answers (null under two). */
export type Growth = {state:'growing'|'settled'|'no_voice'|'unsettled';rounds:{round:number;overlap:number|null;top_voice:number|null}[]};
export type Check = {key:string;engine:string;model_id:number|null;created_at?:string;round?:number;growth?:Growth['state'];validation:Validation|null};
export type Evidence = {recording_id:number;start_s:number;end_s:number;origin:'mark'|'check'|'label'|'map';source_path:string;seconds:number;local_time:string};
export type Presence = {status:'confirmed'|'not_found'|'unchecked';evidence:Evidence[];miss:{listened:number;source:'ai'|'search';created_at:string}|null};
export type SearchRef = {id:number;created_at:string};
export type PresenceRow = {recording_id:number;start_s:number;end_s:number;score:number|null;models:{engine:string;rank:number;score:number}[];
  run_id:number|null;marked:boolean;source:AudioSource;local_time:string};
// BirdNET range model for the site: 'unusual' species are not on its list; missing or null means no judgement.
export type Region = 'usual'|'unusual'|null;
export type PresenceList = {species:Species;source:'ai'|'search';run_id:number|null;rows:PresenceRow[];has_ai:boolean;ai_covers?:boolean;start_date:string;end_date:string;
  site:{id:number;name:string;timezone:string};presence:Presence;region?:Region};
/** Where and in which round a species AI was made, and the AI it grew from (`parent`): its name as a version. */
export type VersionName = {site:string|null;round:number;file?:string|null;parent?:{model_id?:number|null;site:string|null;round:number|null;file?:string|null}|null;made_at?:string|null};
/** The test-sound report of a species (GET …/test-sounds), as far as pages read it. */
export type TestReport = {state:'none'}|{state:'ready';sounds:{heard:number;not_heard:number};rounds:{round:number;found:number|null;false:number|null;current:boolean;made_from_test:boolean}[]};
/** How an AI did on test sounds: `found` of `heard`, `false` of `not_heard`. */
export type TestScore = {heard:number;not_heard:number;found:number;false:number};
export type Material = {positive_5s:number;negative_5s:number;sites:string[]};
/** A copy of an AI made elsewhere: from another site of this PC (`from_model`) or from a file (`file`). */
export type Imported = VersionName&{file:string|null;from_model?:number|null;engine:string;warning:string|null;score?:TestScore|null};
export type CustomModel = {imported?:Imported|null;parent?:VersionName['parent'];id:number;round:number;created_at:string;dataset_id:number;run_id:number|null;species:Species;
  scope:{site_id:number;start_date:string;end_date:string;recording_ids:number[]};
  training:{other_sites?:{positive_5s:number;negative_5s:number;sites:number}|null;inherited?:Material|null;positive_5s:number;negative_5s:number};inference:{scored_5s:number};applicable:boolean;reason:string|null;growth:Growth};
/** A 5-second sound to answer on the species-AI page; `score` is null in the list of answered sounds. */
export type LabelRow = {test?:boolean;segment_id:number;start_s:number;end_s:number;recording_id:number;path:string;seconds:number;local_time:string;
  score:number|null;label:'target'|'other'|null};
/** The species-AI page. `batch` and `source` come only when a round is asked for; source null = nothing to choose sounds by. */
export type SpeciesAI = {species:Species;start_date:string;end_date:string;model:CustomModel|null;/** The species AI's own check (step 3 of its page); status 'stale' once the AI was made again. */check:Validation|null;source:'custom'|'search'|'builtin'|null;engine:string;
  /** A species-AI batch also has one sound for each example of the species' search. */
  by_example:boolean;search_examples:number;batch:LabelRow[]|null;answered:LabelRow[];needed:number;examples:{positive_5s:number;negative_5s:number;other_answers:number;no_bird_answers:number};
  requirements:{positive_5s:number;negative_5s:number;total_5s:number};can_train:boolean;reasons:string[];
  /** Test sounds come first: 'collecting' until they are frozen ('fixed'); 'skipped' is a species grown before that; `test` counts them and names the aims. */
  phase:'collecting'|'fixed'|'skipped';test:{heard:number;not_heard:number;answered:number;aim_heard:number;aim_not_heard:number;ask_after:number;enough:number;complete:boolean};sharing?:{all_sites:boolean;positive_5s:number;negative_5s:number;sites:number};
  /** The material of the AI it grows from, taken when the AI is made here. */
  inherited?:Material|null};
/** The species' AIs on this PC as versions (GET …/ai-versions). */
export type AIVersions = {current_id:number|null;
  versions:(VersionName&{id:number;here:boolean;at_this_site:boolean;used:{site:string;site_id:number;round:number}[];engine:string;warning:boolean;score:TestScore|null})[];
  others:(VersionName&{id:number;engine:string;warning:boolean;training:{positive_5s:number;negative_5s:number};score:TestScore|null})[];
  newer:(VersionName&{id:number;training:{positive_5s:number;negative_5s:number;inherited?:Material|null};
    tests:{heard:number;not_heard:number;now:{found:number;false:number};new:{found:number;false:number}}|null;
    differ:{segment_id:number;recording_id:number;start_s:number;end_s:number;path:string;seconds:number;local_time:string;heard:boolean;now:boolean;new:boolean}[]})|null};
/** A version's name: "North woods, round 3"; a file's AI without its site's name says so. */
export const versionName = (v:{site:string|null;round:number|null}) => v.site?fill('vnName',{site:v.site,n:v.round??1}):fill('vnNoSite',{n:v.round??1});
/** The name with what it grew from: "South woods, round 2 (grown from North woods, round 3)". */
export const versionFull = (v:VersionName) => v.parent?fill('vnFrom',{name:versionName(v),parent:versionName(v.parent)}):versionName(v);
export type ModelRow = {engine:string;key:string;model_id:number|null;created_at?:string;round?:number;candidate_windows?:number|null;candidate_days?:number|null;eligible_windows?:number|null;
  /** null: the applicable check ended undecided, so this AI's detections are not counted. */
  threshold?:number|null;validation:Validation|null};
export type Overview = ReportOptions & {dataset:{id:number;name:string};site:{id:number;name:string;timezone:string};fingerprint:string;revision:string;mixed_models:boolean;location_stale:boolean;
  start_date:string;end_date:string;records:number;recorded_seconds:number;analysed_recordings:number;pending_analysis:Work[];
  species:{species:Species;models:ModelRow[];search:SearchRef|null;presence:Presence;region?:Region}[]};
export type SpeciesResult = Omit<Overview,'species'> & {species:Species;report:Report|null;checks:Check[];selected:string|null;search:SearchRef|null;presence:Presence;region?:Region};
// `{n|one|other}` in a message picks the singular word when the value is 1 (English plurals).
export const fill = (key:string,values:Record<string,string|number>) => Object.entries(values).reduce((text,[name,value])=>text.replaceAll(`{${name}}`,String(value))
  .replace(new RegExp(`\\{${name}\\|([^|}]*)\\|([^}]*)\\}`,'g'),(_,one:string,other:string)=>Number(value)===1?one:other),t(key));
/** "Still missing: …" naming each missing item by its label key; empty when nothing is missing. */
export const missingText = (keys:(string|false)[]) => {const names=keys.filter(key=>key!==false).map(t);return names.length?fill('uStillMissing',{items:new Intl.ListFormat(app.locale).format(names)}):'';};
/** After a press with missing input: bring the first marked field into view (opening its section) and put the cursor in it. */
export async function focusMissing(root:ParentNode=document) {
  await tick();
  const field=root.querySelector<HTMLElement>('[aria-invalid="true"], .field-error[tabindex]');
  const section=field?.closest('details');if(section)section.open=true;
  field?.scrollIntoView({block:'center'});field?.focus({preventScroll:true});
}
export const customName = (model:{created_at?:string;round?:number}) => fill('customModelNumber',{n:model.round??1,date:new Intl.DateTimeFormat(app.locale,{month:'numeric',day:'numeric'}).format(new Date(`${model.created_at?.replace(' ','T')}Z`))});
const modelOf = (engine:string) => app.ai.models.find(model=>model.id===engine);
/** Display name from GET /models; 'custom' is an AI made for one species. */
export const modelName = (engine:string) => engine==='custom'?t('customModel'):modelOf(engine)?.name??engine;
/** The AI's name with a one-line note on who made it, for its first mention on a page. */
export const modelIntro = (engine:string) => engine==='custom'?modelName(engine):fill('aiAbout',{name:modelName(engine),about:t(`aiAbout_${engine.split('-')[0]}`)});
export const checkName = (check:{engine:string;created_at?:string;round?:number}) => check.engine==='custom'?customName(check):modelName(check.engine);
/** Window length of a model's intervals from GET /models (BirdNET 3 s); species models are 5 s. */
/** Analysis one AI still has to do: how many recordings and their total length in seconds. */
export type Work = {engine:string;recordings:number;seconds:number};
/** Expected analysis time on this PC, or null when there is nothing to do or an AI has no speed measurement yet. */
export function analysisSeconds(work:Work[]) {
  let total=app.ai.seconds_per_job??0;
  for(const {engine,recordings,seconds} of work) {
    const model=modelOf(engine);
    if(model?.seconds_per_file==null||model.seconds_per_hour==null)return null;
    total+=recordings*model.seconds_per_file+seconds/3600*model.seconds_per_hour;
  }
  return work.length?total:null;
}
/** A rough length of time: seconds under a minute, minutes up to 90, then hours with one decimal. */
export const roughTime = (seconds:number) => seconds<60?fill('timeSeconds',{n:Math.max(1,Math.round(seconds))})
  :seconds<=5400?fill('timeMinutes',{n:Math.round(seconds/60)}):fill('timeHours',{n:(seconds/3600).toFixed(1)});
export const windowSeconds = (engine:string) => modelOf(engine)?.window_s??5;
/** Why no threshold was decided: "no sound answered Heard" only when that holds for every given check. */
const undecidedText = (checks:Validation[]) => t(checks.every(value=>value.results[value.engine]?.reason==='no_target')?'cUndecidedNoTarget':'cUndecided');
/** Why a check no longer applies. After the species AI was made again, the words follow how far it has grown (`growth`, where known). */
export const staleReason = (value:Validation,growth?:Growth['state']) => value.model_reason==='custom_model_rebuilt'&&growth?`sGrow_${growth}`:value.model_reason;
/** Progress as the question page shows it: new checks ask the two top score groups first and stop there when no call is heard,
 * so until those are answered the goal is that first block ("5 / 20 sounds (up to 60)"). */
export function checkProgress(value:Validation) {
  const first=value.method==='detection_top_bin_v2'?value.counts.bins.slice(4).reduce((sum,bin)=>sum+bin.total,0):0;
  const inFirst=value.counts.answered<first,goal=inFirst?first:value.counts.total;
  return {goal,text:fill(inFirst?'vProgressFirst':'vProgress',{answered:value.counts.answered,first,total:value.counts.total})};
}
/** Plain status of one model's check. */
export function checkText(value:Validation|null,growth?:Growth['state']) {
  // A check opened but not answered yet has not started.
  if(!value||value.status==='in_progress'&&!value.counts.answered)return t('cNotStarted');
  if(value.status==='in_progress')return fill('cInProgress',{progress:checkProgress(value).text});
  if(value.status==='stale')return t(value.model_reason==='custom_model_rebuilt'?staleReason(value,growth)!:'wStale');
  return value.status==='decided'?t('cDecided'):undecidedText([value]);
}
/** The one button into a check: start, continue (answered/total), or see the result. */
export function listenLabel(value:Validation|null) {
  if(!value||!value.applicable)return t(value?'wCheckAgain':'cStart');
  if(value.stage==='complete')return t('wValidationDone');
  if(!value.counts.answered)return t('cStart');
  return fill('vListenContinue',{answered:value.counts.answered,total:checkProgress(value).goal});
}
/** Presence in words with a leading symbol, so it never relies on colour alone. */
export function presenceText(presence:Presence) {
  if(presence.status==='confirmed')return `✓ ${fill('pConfirmed',{n:presence.evidence.length})}`;
  if(presence.status==='not_found')return `− ${fill('pNotFound',{n:presence.miss!.listened,order:t(presence.miss!.source==='search'?'pOrderSearch':'pOrderAi')})}`;
  return `○ ${t('pUnchecked')}`;
}
export const rangeQuery = (first:string,last:string) => new URLSearchParams({start_date:first,end_date:last}).toString();
/** A path with its query, without a bare `?` (the hash router does not find a path ending in one). */
export const withQuery = (path:string,query:string) => query?`${path}?${query}`:path;
// About 10 detected intervals in 30 hours of recording; below that a species is more likely an AI mistake than present (review A2, A5).
export const fewBelow = (recordedSeconds:number) => recordedSeconds/3600/3;
/** Site list sections: confirmed by listening, listened but not confirmed, worth checking, few detections and nothing done yet,
 * or not expected here by the BirdNET range model. A person's listening outranks the range model. */
export function speciesGroup(row:Overview['species'][number],recordedSeconds:number):'confirmed'|'missed'|'check'|'few'|'rare' {
  if(row.presence.status==='confirmed')return 'confirmed';
  if(row.presence.status==='not_found')return 'missed';
  if(row.region==='unusual')return 'rare';
  const most=Math.max(0,...row.models.map(model=>model.candidate_windows??0));
  const touched=row.search||row.models.some(model=>model.validation);
  return !touched&&most<fewBelow(recordedSeconds)?'few':'check';
}
type Checked = {engine:string;created_at?:string;round?:number;validation:Validation|null};
export const share = (value:number|null) => value==null?'—':new Intl.NumberFormat(app.locale,{style:'percent',maximumSignificantDigits:2}).format(value);
/** Models whose counting rule was decided by checking, each with its share of correct detections when known. */
export const decidedModels = (models:Checked[]) =>
  models.filter(model=>model.validation?.applicable&&model.validation.status==='decided').map(model=>{
    const precision=model.validation!.results[model.validation!.engine]?.precision?.value;
    return precision==null?checkName(model):fill('wModelPrecision',{model:checkName(model),p:share(precision)});
  });
/** "When and how much" of a species. One decided AI is enough (B3); undecided only when no checked AI decided and none is in progress. */
export function activity(models:Checked[]):'decided'|'in_progress'|'undecided'|'not_started' {
  const states=models.map(({validation:value})=>value?.applicable&&(value.status!=='in_progress'||value.counts.answered)?value.status:'not_started');
  return (['decided','in_progress','undecided'] as const).find(state=>states.includes(state))??'not_started';
}
const inProgress = (models:Checked[]) => models.map(model=>model.validation).find(value=>value?.applicable&&value.status==='in_progress'&&value.counts.answered>0);
/** activityText with a leading symbol, so the state never relies on colour alone. */
export const activityLine = (models:Checked[]) => {const state=activity(models);return `${state==='decided'?'✓':state==='undecided'?'−':'○'} ${activityText(models)}`;};
export function activityText(models:Checked[]) {
  const state=activity(models),open=inProgress(models);
  if(state==='decided')return fill('wDecidedModels',{models:decidedModels(models).join(app.locale==='ja'?'・':', ')});
  if(open)return fill('cInProgress',{progress:checkProgress(open).text});
  return state==='undecided'?undecidedText(models.flatMap(model=>model.validation?.applicable&&model.validation.status==='undecided'?[model.validation]:[])):t('wNoDecided');
}
/** B3: the AI with more detections is the one to check; the others are optional. */
export const defaultModel = <T extends {candidate_windows?:number|null}>(models:T[]) =>
  models.reduce<T|undefined>((best,model)=>!best||(model.candidate_windows??0)>(best.candidate_windows??0)?model:best,undefined);
// The agreed check (A2): up to 60 sounds for one AI, about 8–10 seconds per sound. New checks never add more sounds.
export const CHECK_SOUNDS = 60;
type Countable = Checked & {candidate_windows?:number|null;eligible_windows?:number|null};
/** Sounds still to answer before "when and how much" is settled. A small site has fewer intervals than 60. */
export function remainingSounds(models:Countable[]):number {
  const state=activity(models),open=inProgress(models);
  if(state==='decided'||state==='undecided')return 0;
  if(open){const {goal}=checkProgress(open);return goal-open.counts.answered;}
  const population=defaultModel(models)?.eligible_windows??Infinity;
  return Math.min(CHECK_SOUNDS,population);
}
// One value when both ends round to the same text (not "3 min–3 min").
export const workload = (sounds:number) => {const low=duration(sounds*8),high=duration(sounds*10);
  return {time:low===high?low:`${low}${app.locale==='ja'?'〜':'–'}${high}`};};
// A page's own parameters and its `return=` (`back`: the in-app page to go back to). `return` is always the last
// parameter and takes the rest of the query: after a reload or a step in the history the hash router hands the hash
// over decoded, and the `&` of the return path could not be told from the page's own.
export const pageQuery = (url:URL) => {const text=url.hash.slice(1)||url.pathname+url.search,query=text.includes('?')?text.slice(text.indexOf('?')+1):'',at=query.search(/(^|&)return=/);
  let back=at<0?'':query.slice(query.indexOf('return=',at)+7);
  try{back=decodeURIComponent(back);}catch{/* already decoded */}
  return {params:new URLSearchParams(at<0?query:query.slice(0,at)),back};};
// Anything not matching `allowed` is ignored.
export const returnPath = (url:URL,allowed:RegExp) => {const path=pageQuery(url).back;return allowed.test(path)?path:'';};
export const appHref = (path:string) => `${resolve('/')}${path.slice(1)}`;
