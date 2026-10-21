import { resolve } from '$app/paths';
import type { Species } from './species';
import type { Report, ReportOptions } from './reports';
import type { AudioSource } from './AudioRegion.svelte';
import { app,t,duration } from './app.svelte';
export type Metric = { value: number | null; low: number | null; high: number | null };
export type CheckStatus = 'not_started'|'in_progress'|'decided'|'undecided'|'stale';
/** One check's estimates with 90% ranges; present only after every sound is answered. */
export type QualityResult = { status:'decided'|'undecided'; threshold:number|null; reason:string|null; density:Metric; count:Metric;
  precision:Metric|null; recall:Metric|null; recall_status:'estimated'|'not_estimable'|'not_estimable_rare'; auprc:Metric|null; multiplier:Metric|null;
  unknown:number; disagreement:boolean; answers_per_bin:{heard:number;not_heard:number;unknown:number}[] };
/** One model's check for one species: 60 mixed sounds, and 18 more when the result is too uncertain. */
export type Validation = { id:number; dataset_id:number; species:Species; stage:'sample'|'extension'|'complete'; status:CheckStatus;
  model:string; engine:'birdnet'|'perch'|'custom'; model_id:number|null; results:Record<string,QualityResult>;
  counts:{total:number;answered:number;unknown:number};
  question:{id:number;position:number;verdict:string|null;version:number;source:AudioSource;start_s:number;end_s:number;review_position:number;review_total:number}|null;
  history:{id:number;position:number;verdict:string}[]; can_extend:boolean; extended:boolean; model_reason?:string|null;
  start_date:string; end_date:string; window_s:number; population:number; applicable:boolean; created_at:string };
export type Check = {key:string;engine:'birdnet'|'perch'|'custom';model_id:number|null;created_at?:string;validation:Validation|null};
export type Evidence = {recording_id:number;start_s:number;end_s:number;origin:'mark'|'check';source_path:string;seconds:number;local_time:string};
export type Presence = {status:'confirmed'|'not_found'|'unchecked';evidence:Evidence[];miss:{listened:number;source:'ai'|'search';created_at:string}|null};
export type SearchRef = {id:number;created_at:string};
export type PresenceRow = {recording_id:number;start_s:number;end_s:number;score:number|null;models:{engine:string;rank:number;score:number}[];
  run_id:number|null;marked:boolean;source:AudioSource;local_time:string};
// BirdNET range model for the site: 'unusual' species are not on its list; missing or null means no judgement.
export type Region = 'usual'|'unusual'|null;
export type PresenceList = {species:Species;source:'ai'|'search';run_id:number|null;rows:PresenceRow[];has_ai:boolean;start_date:string;end_date:string;
  site:{id:number;name:string;timezone:string};presence:Presence;region?:Region};
export type CustomModel = {id:number;created_at:string;dataset_id:number;run_id:number;species:Species;
  scope:{site_id:number;start_date:string;end_date:string;recording_ids:number[]};
  training:{positive_5s:number;negative_5s:number;negative_from_checks:number};inference:{scored_5s:number};applicable:boolean;reason:string|null};
export type SearchHit = {segment_id:number;similarity:number;start_s:number;end_s:number;recording_id:number;path:string;seconds:number;local_time:string;label:'target'|'other'|null};
export type CustomModelReadiness = {run_id:number;species:Species;hits:SearchHit[];hit_count:number;
  examples:{positive_5s:number;negative_5s:number;negative_from_checks:number};requirements:{positive_5s:number;negative_5s:number};
  can_train:boolean;reasons:string[];models:CustomModel[]};
export type ModelRow = {engine:'birdnet'|'perch'|'custom';key:string;model_id:number|null;created_at?:string;candidate_windows?:number|null;candidate_days?:number|null;eligible_windows?:number|null;
  threshold?:number|null;validation:Validation|null};
export type Overview = ReportOptions & {dataset:{id:number;name:string};site:{id:number;name:string;timezone:string};fingerprint:string;revision:string;mixed_models:boolean;location_stale:boolean;
  start_date:string;end_date:string;records:number;recorded_seconds:number;analysed_recordings:number;
  species:{species:Species;models:ModelRow[];search:SearchRef|null;presence:Presence;region?:Region}[]};
export type SpeciesResult = Omit<Overview,'species'> & {species:Species;report:Report|null;checks:Check[];selected:string|null;search:SearchRef|null;presence:Presence;region?:Region};
// `{n|one|other}` in a message picks the singular word when the value is 1 (English plurals).
export const fill = (key:string,values:Record<string,string|number>) => Object.entries(values).reduce((text,[name,value])=>text.replaceAll(`{${name}}`,String(value))
  .replace(new RegExp(`\\{${name}\\|([^|}]*)\\|([^}]*)\\}`,'g'),(_,one:string,other:string)=>Number(value)===1?one:other),t(key));
export const customName = (model:{created_at?:string}) => fill('customModelNumber',{date:new Intl.DateTimeFormat(app.locale,{month:'numeric',day:'numeric'}).format(new Date(`${model.created_at?.replace(' ','T')}Z`))});
export const modelName = (engine:string) => engine==='birdnet'?'BirdNET':engine==='perch'?'Perch':t('customModel');
export const checkName = (check:{engine:string;created_at?:string}) => check.engine==='custom'?customName(check):modelName(check.engine);
/** Window length of a model's intervals: BirdNET 3 s, Perch and species models 5 s. */
export const windowSeconds = (engine:string) => engine==='birdnet'?3:5;
/** Plain status of one model's check. */
export function checkText(value:Validation|null) {
  if(!value)return t('cNotStarted');
  if(value.status==='in_progress')return fill('cInProgress',{answered:value.counts.answered,total:value.counts.total});
  return t(value.status==='stale'?'wStale':value.status==='decided'?'cDecided':'cUndecided');
}
/** The one button into a check: start, continue (answered/total), or see the result. */
export function listenLabel(value:Validation|null) {
  if(!value||!value.applicable)return t(value?'wCheckAgain':'cStart');
  if(value.stage==='complete')return t('wValidationDone');
  return fill('vListenContinue',{answered:value.counts.answered,total:value.counts.total});
}
/** Presence in words with a leading symbol, so it never relies on colour alone. */
export function presenceText(presence:Presence) {
  if(presence.status==='confirmed')return `✓ ${fill('pConfirmed',{n:presence.evidence.length})}`;
  if(presence.status==='not_found')return `− ${fill('pNotFound',{n:presence.miss!.listened})}`;
  return `○ ${t('pUnchecked')}`;
}
export const rangeQuery = (first:string,last:string) => new URLSearchParams({start_date:first,end_date:last}).toString();
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
type Checked = {engine:string;created_at?:string;validation:Validation|null};
/** Models whose counting rule was decided by checking. */
export const decidedModels = (models:Checked[]) =>
  models.filter(model=>model.validation?.applicable&&model.validation.status==='decided').map(checkName);
/** "When and how much" of a species. One decided AI is enough (B3); undecided only when no checked AI decided and none is in progress. */
export function activity(models:Checked[]):'decided'|'in_progress'|'undecided'|'not_started' {
  const states=models.map(model=>model.validation?.applicable?model.validation.status:'not_started');
  return (['decided','in_progress','undecided'] as const).find(state=>states.includes(state))??'not_started';
}
const inProgress = (models:Checked[]) => models.map(model=>model.validation).find(value=>value?.applicable&&value.status==='in_progress');
export function activityText(models:Checked[]) {
  const state=activity(models),open=inProgress(models);
  if(state==='decided')return fill('wDecidedModels',{models:decidedModels(models).join(app.locale==='ja'?'・':', ')});
  if(open)return fill('cInProgress',{answered:open.counts.answered,total:open.counts.total});
  return t(state==='undecided'?'cUndecided':'wNoDecided');
}
/** B3: the AI with more detections is the one to check; the others are optional. */
export const defaultModel = <T extends {candidate_windows?:number|null}>(models:T[]) =>
  models.reduce<T|undefined>((best,model)=>!best||(model.candidate_windows??0)>(best.candidate_windows??0)?model:best,undefined);
// The agreed check (A2): 60 sounds for one AI, 18 more only when needed, about 8–10 seconds per sound.
export const CHECK_SOUNDS = 60, EXTRA_SOUNDS = 18;
type Countable = Checked & {candidate_windows?:number|null;eligible_windows?:number|null};
/** Sounds still to answer before "when and how much" is settled, and the most that may be added. A small site has fewer intervals than 60. */
export function remainingSounds(models:Countable[]):[sounds:number,extra:number] {
  const state=activity(models),open=inProgress(models);
  if(state==='decided'||state==='undecided')return [0,0];
  if(open)return [open.counts.total-open.counts.answered,open.extended||open.population<=open.counts.total?0:EXTRA_SOUNDS];
  const population=defaultModel(models)?.eligible_windows??Infinity;
  return [Math.min(CHECK_SOUNDS,population),population>CHECK_SOUNDS?EXTRA_SOUNDS:0];
}
export const workload = (sounds:number,extra:number) => ({low:duration(sounds*8),high:duration((sounds+extra)*10)});
// `return=` carries the in-app page to go back to (hash routing); anything not matching `allowed` is ignored.
export const returnPath = (url:URL,allowed:RegExp) => {const path=new URL(url.hash.slice(1)||url.pathname+url.search,url).searchParams.get('return')??'';return allowed.test(path)?path:'';};
export const appHref = (path:string) => `${resolve('/')}${path.slice(1)}`;
