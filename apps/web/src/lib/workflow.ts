import { resolve } from '$app/paths';
import type { Species } from './species';
import type { Report, ReportOptions } from './reports';
import type { AudioSource } from './AudioRegion.svelte';
import { app,t } from './app.svelte';
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
export type PresenceList = {species:Species;source:'ai'|'search';run_id:number|null;rows:PresenceRow[];has_ai:boolean;start_date:string;end_date:string;
  site:{id:number;name:string;timezone:string};presence:Presence};
export type CustomModel = {id:number;created_at:string;dataset_id:number;run_id:number;species:Species;
  scope:{site_id:number;start_date:string;end_date:string;recording_ids:number[]};
  training:{positive_5s:number;negative_5s:number;negative_from_checks:number};inference:{scored_5s:number};applicable:boolean;reason:string|null};
export type SearchHit = {segment_id:number;similarity:number;start_s:number;end_s:number;recording_id:number;path:string;seconds:number;local_time:string;label:'target'|'other'|null};
export type CustomModelReadiness = {run_id:number;species:Species;hits:SearchHit[];hit_count:number;
  examples:{positive_5s:number;negative_5s:number;negative_from_checks:number};requirements:{positive_5s:number;negative_5s:number};
  can_train:boolean;reasons:string[];models:CustomModel[]};
export type ModelRow = {engine:'birdnet'|'perch'|'custom';key:string;model_id:number|null;created_at?:string;candidate_windows?:number|null;candidate_days?:number|null;
  threshold?:number|null;validation:Validation|null};
export type Overview = ReportOptions & {dataset:{id:number;name:string};site:{id:number;name:string;timezone:string};fingerprint:string;revision:string;mixed_models:boolean;location_stale:boolean;
  start_date:string;end_date:string;records:number;recorded_seconds:number;analysed_recordings:number;
  species:{species:Species;models:ModelRow[];search:SearchRef|null;presence:Presence}[]};
export type SpeciesResult = Omit<Overview,'species'> & {species:Species;report:Report|null;checks:Check[];selected:string|null;search:SearchRef|null;presence:Presence};
export const fill = (key:string,values:Record<string,string|number>) => Object.entries(values).reduce((text,[name,value])=>text.replaceAll(`{${name}}`,String(value)),t(key));
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
// Below this many detected intervals (by any AI), a species is more likely an AI mistake than present (review A2).
export const FEW_DETECTIONS = 10;
/** Site list sections: confirmed by listening, worth checking, or few detections and nothing done yet. */
export function speciesGroup(row:Overview['species'][number]):'confirmed'|'check'|'few' {
  if(row.presence.status==='confirmed')return 'confirmed';
  const most=Math.max(0,...row.models.map(model=>model.candidate_windows??0));
  const touched=row.search||row.presence.miss||row.models.some(model=>model.validation);
  return !touched&&most<FEW_DETECTIONS?'few':'check';
}
/** Models whose counting threshold was decided by checking. */
export const decidedModels = (models:{engine:string;created_at?:string;validation:Validation|null}[]) =>
  models.filter(model=>model.validation?.applicable&&model.validation.status==='decided').map(checkName);
// `return=` carries the in-app page to go back to (hash routing); anything not matching `allowed` is ignored.
export const returnPath = (url:URL,allowed:RegExp) => {const path=new URL(url.hash.slice(1)||url.pathname+url.search,url).searchParams.get('return')??'';return allowed.test(path)?path:'';};
export const appHref = (path:string) => `${resolve('/')}${path.slice(1)}`;
