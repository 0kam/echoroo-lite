import { resolve } from '$app/paths';
import type { Species } from './species';
import type { Report, ReportOptions } from './reports';
import type { AudioSource } from './AudioRegion.svelte';
import { app,t } from './app.svelte';
export type Metric = { value: number | null; low: number | null; high: number | null };
export type AuditSample = {total:number;answered:number;target:number;unknown:number;population:number};
export type QualityResult = { model_sha256: string; threshold: number | null; adjusted: boolean; reason?: string|null; precision?: Metric; recall?: Metric;
  estimated_window_rate?: Metric; eligible_windows?: number; reviewed?: number; unknown?: number; goal_met: boolean; status: string;
  precision_lower_95?:number|null;precision_complete?:boolean;recall_complete?:boolean;precision_sample?:AuditSample;recall_sample?:AuditSample };
export type CheckStatus = 'not_started'|'stale'|'calibrating'|'undecided'|'checking'|'goal_met'|'goal_not_met';
/** One model's check for one species: ① set the threshold, ② accuracy, ③ missed calls. */
export type Validation = { id: number; dataset_id: number; species: Species; stage: 'calibration' | 'evaluation' | 'complete'; status: CheckStatus;
  model:string;engine:'birdnet'|'perch'|'custom';model_id:number|null;thresholds:Record<string,{threshold:number|null;adjusted:boolean;reason?:string|null}>;
  counts: Record<string,{total:number;answered:number}>; models: Record<string,string>; results: Record<string,QualityResult>;
  question: {id:number;position:number;phase:'calibration'|'evaluation';verdict:string|null;version:number;source:AudioSource;start_s:number;end_s:number;
    samples:{engine:string;sample:'precision'|'recall'}[];review_position:number;review_total:number} | null;
  model_reason?:string|null;split_unit:string;
  history:{id:number;position:number;verdict:string;samples:{engine:string;sample:'precision'|'recall'}[]}[];uncertainty:string;
  start_date:string;end_date:string;target_precision:number;calibration_target_precision:number;window_s:number;population:number;applicable:boolean;created_at:string;
  audit_step?:'precision'|'recall';can_start_recall?:boolean;recall_started?:boolean };
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
export const validationPhase = (value:Validation|null) => value?.audit_step==='recall'?'recall':value?.stage==='calibration'?'calibration':'evaluation';
export const fill = (key:string,values:Record<string,string|number>) => Object.entries(values).reduce((text,[name,value])=>text.replaceAll(`{${name}}`,String(value)),t(key));
export const customName = (model:{created_at?:string}) => fill('customModelNumber',{date:new Intl.DateTimeFormat(app.locale,{month:'numeric',day:'numeric'}).format(new Date(`${model.created_at?.replace(' ','T')}Z`))});
export const modelName = (engine:string) => engine==='birdnet'?'BirdNET':engine==='perch'?'Perch':t('customModel');
export const checkName = (check:{engine:string;created_at?:string}) => check.engine==='custom'?customName(check):modelName(check.engine);
const percentText = (value:number|null|undefined) => value===null||value===undefined?'—':`${Math.round(value*100)}%`;
/** Plain status of one model's check: goal met, goal missed, not decided, in progress, or not yet. */
export function checkText(value:Validation|null) {
  if(!value)return t('cNotStarted');
  const result=value.results[value.engine];
  const count=(phase:string)=>fill('cProgress',{answered:value.counts[phase].answered,total:value.counts[phase].total});
  switch(value.status){
    case 'stale':return t('wStale');
    case 'calibrating':return `${t('cCalibrating')} ${count('calibration')}`;
    case 'undecided':return `${t('cUndecided')}${value.thresholds[value.engine]?.reason?` · ${t('reason_'+value.thresholds[value.engine].reason)}`:''}`;
    case 'checking':return `${t('cChecking')} ${count('evaluation')}`;
    default:return fill(value.status==='goal_met'?'cGoalMet':'cGoalNotMet',{p:percentText(result?.precision?.value),target:percentText(value.target_precision)});
  }
}
/** The recall guide is advice only: below it, the check suggests building a model, but the goal follows accuracy alone. */
export const RECALL_GUIDE = .8;
export function recallText(value:Validation|null) {
  const result=value?.results[value.engine],recall=result?.recall,sample=result?.recall_sample;
  if(!value?.applicable||!result?.recall_complete||!recall||(recall.value==null&&recall.low==null))return '';
  const p=rangeText(recall);
  const text=recall.value==null?fill('wRecallOnly',{p}):fill(recall.value<RECALL_GUIDE?'wRecallBelow':'wRecallMet',{p,target:percentText(RECALL_GUIDE)});
  return sample?`${text} ${fill('wRecallSample',{population:sample.population.toLocaleString(app.locale),total:sample.total,target:sample.target})}`:text;
}
/** The accuracy estimate with its range, shown next to the goal line that repeats only the point value. */
export function precisionText(value:Validation|null) {
  const precision=value?.results[value.engine]?.precision;
  return value?.applicable&&value.results[value.engine]?.precision_complete&&precision&&(precision.value!=null||precision.low!=null)?fill('wPrecisionAbout',{p:rangeText(precision)}):'';
}
/** A 95% interval in plain words; above WIDE_RANGE it also says the range is wide. */
const WIDE_RANGE = .3;
export function rangeText(metric:Metric) {
  if(metric.low===null||metric.high===null)return percentText(metric.value);
  const range=fill(metric.high-metric.low>WIDE_RANGE?'wRangeWide':'wRange',{low:percentText(metric.low),high:percentText(metric.high)});
  return metric.value===null?range:fill('wAbout',{p:percentText(metric.value),range});
}
/** The one button into a check: start, continue (answered/total), or see the result. */
export function listenLabel(value:Validation|null) {
  if(!value||!value.applicable)return t(value?'wCheckAgain':'cStart');
  if(value.stage==='complete')return t('wValidationDone');
  const count=value.counts[validationPhase(value)];
  return fill('vListenContinue',{answered:count.answered,total:count.total});
}
export function presenceText(presence:Presence) {
  if(presence.status==='confirmed')return fill('pConfirmed',{n:presence.evidence.length});
  if(presence.status==='not_found')return fill('pNotFound',{n:presence.miss!.listened});
  return t('pUnchecked');
}
export const interval = (value:Metric) => value.low===null||value.high===null?'—':`${(value.low*100).toFixed(1)}–${(value.high*100).toFixed(1)}%`;
export const rangeQuery = (first:string,last:string) => new URLSearchParams({start_date:first,end_date:last}).toString();
// Below this many 15-second detections (by any AI), a species is more likely an AI mistake than present (review A2).
export const FEW_DETECTIONS = 10;
/** Site list sections: confirmed by listening, worth checking, or few detections and nothing done yet. */
export function speciesGroup(row:Overview['species'][number]):'confirmed'|'check'|'few' {
  if(row.presence.status==='confirmed')return 'confirmed';
  const most=Math.max(0,...row.models.map(model=>model.candidate_windows??0));
  const touched=row.search||row.presence.miss||row.models.some(model=>model.validation);
  return !touched&&most<FEW_DETECTIONS?'few':'check';
}
/** Models whose counts met the accuracy goal: their time charts are reliable. */
export const reliableModels = (models:{engine:string;created_at?:string;validation:Validation|null}[]) =>
  models.filter(model=>model.validation?.applicable&&model.validation.status==='goal_met').map(checkName);
// `return=` carries the in-app page to go back to (hash routing); anything not matching `allowed` is ignored.
export const returnPath = (url:URL,allowed:RegExp) => {const path=new URL(url.hash.slice(1)||url.pathname+url.search,url).searchParams.get('return')??'';return allowed.test(path)?path:'';};
export const appHref = (path:string) => `${resolve('/')}${path.slice(1)}`;
