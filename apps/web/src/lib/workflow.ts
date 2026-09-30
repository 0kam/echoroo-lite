import { resolve } from '$app/paths';
import type { Species } from './species';
import type { Report, ReportOptions } from './reports';
import type { AudioSource } from './AudioRegion.svelte';
import type { CallRange } from './annotations';
import { app,t } from './app.svelte';
import { api } from './api.svelte';
export type Metric = { value: number | null; low: number | null; high: number | null };
export type ReferenceMetric = {value:number|null;positive?:number;predicted?:number;found?:number;present?:number};
export type AuditSample = {total:number;answered:number;target:number;unknown:number;population:number};
export type QualityResult = { model_sha256: string; threshold: number | null; adjusted: boolean; precision: Metric; recall: Metric;
  estimated_window_rate: Metric; eligible_windows: number; reviewed: number; unknown: number; unknown_rate: number | null; goal_met: boolean; status: string;
  precision_lower_95?:number|null;precision_complete?:boolean;recall_complete?:boolean;precision_sample?:AuditSample;recall_sample?:AuditSample };
export type Validation = { id: number; dataset_id: number; species: Species; stage: 'calibration' | 'evaluation' | 'complete';
  kind:'detection'|'search'|'custom';run_id:number|null;model_id:number|null;thresholds:Record<string,{threshold:number|null;adjusted:boolean}>;
  counts: Record<string,{total:number;answered:number}>; models: Record<string,string>; results: Record<string,QualityResult>;
  question: {id:number;position:number;phase:'calibration'|'evaluation';verdict:string|null;version:number;source:AudioSource;start_s:number;end_s:number;ranges:CallRange[];
    samples:{engine:string;sample:'precision'|'recall'}[];review_position:number;review_total:number} | null;
  annotation_session_id:number|null;annotation_source_current:boolean;split_unit:string|null;
  model_reason?:string|null;
  history:{id:number;position:number;verdict:string;samples:{engine:string;sample:'precision'|'recall'}[]}[];evaluated_count:number;uncertainty:string;
  start_date:string;end_date:string;target_precision:number;calibration_target_precision?:number;window_s:number;population:number;applicable:boolean;created_at:string;
  method?:string;audit_step?:'precision'|'recall';can_start_recall?:boolean;recall_started?:boolean };
export type SearchRun = {id:number;species_id:string;species:Species;created_at:string};
export type CustomModelReadiness = {schema:string;annotation_session_id:number;dataset_id:number;species:Species;
  scope:{site_id:number;start_date:string;end_date:string;fingerprint:string};
  feature:{engine:'perch';model_sha256:string;spec:string;window_s:number};
  split:{unit:string;calibration_recording_ids:number[];evaluation_recording_ids:number[];cv_folds:number;cv_groups:number;evaluation_groups:number};
  examples:{positive_5s:number;negative_5s:number;recordings:number;positive_groups:number;negative_groups:number;label_conflicts:number;missing_embeddings:number;unknown_excluded:number;evaluation_answers_excluded:number;by_source:Record<string,number>};
  requirements:{positive_5s:number;negative_5s:number;cv_groups:number;evaluation_groups:number};can_train:boolean;reasons:string[];
  materials:{label:'target'|'other';origin:'listening'|'search';source:AudioSource;start_s:number;end_s:number;local_time:string}[];
  needs_range:{validation_id:number;item_id:number;version:number;source:AudioSource;start_s:number;end_s:number;local_time:string;ranges:CallRange[]}[];
  answers:{target:number;other:number};models:(CustomModel&{validation:Validation|null;detections?:number})[]};
export type CustomModel = {id:number;status:'ready'|'threshold_unavailable';created_at:string;dataset_id:number;species:Species;
  scope:{site_id:number;start_date:string;end_date:string;recording_ids:number[]};
  feature:{engine:'perch';model_sha256:string;spec:string;window_s:number};
  split:{unit:string};training:{positive_5s:number;negative_5s:number;recordings:number;groups:number;l2:number};
  cv:{status:string;window_s:number;folds:number;threshold:number|null;reference_precision?:ReferenceMetric;reference_recall?:ReferenceMetric;scope_label:string};
  inference:{status:string;scored_5s?:number;complete_15s?:number;missing_5s?:number};
  evaluation:{validation_id:number|null;status:string;window_s:number;answered:number;total:number};applicable:boolean;reason:string|null};
export type Overview = ReportOptions & {dataset:{id:number;name:string};site:{id:number;name:string;timezone:string};fingerprint:string;revision:string;mixed_models:boolean;location_stale:boolean;
  start_date:string;end_date:string;records:number;recorded_seconds:number;analysed_recordings:number;
  species:{species:Species;models:{engine:string;model_sha256:string;candidate_days:number;candidate_windows:number;eligible_windows:number;candidate_rate:number;threshold:number}[];
    searches:(SearchRun&{validation?:Validation|null})[];confirmed:number;annotated:number;validation:Validation|null;custom_models?:(CustomModel&{validation:Validation|null;candidate_windows?:number})[]}[]};
export type SpeciesResult = Omit<Overview,'species'> & {species:Species;report:Report|null;searches:SearchRun[];validation:Validation|null;
  custom_models?:CustomModel[];annotations:(CallRange & {recording_id:number;source_path:string;seconds:number;local_time:string;answer_id:number})[]};
export const validationPhase = (value:Validation|null) => value?.audit_step==='recall'?'recall':value?.stage==='calibration'?'calibration':'evaluation';
export const fill = (key:string,values:Record<string,string|number>) => Object.entries(values).reduce((text,[name,value])=>text.replaceAll(`{${name}}`,String(value)),t(key));
export const customName = (model:{created_at:string}) => fill('customModelNumber',{date:new Intl.DateTimeFormat(app.locale,{month:'numeric',day:'numeric'}).format(new Date(`${model.created_at.replace(' ','T')}Z`))});
export const modelName = (engine:string) => engine==='search'?t('rSearch'):engine==='birdnet'?'BirdNET':engine==='perch'?'Perch':t('customModel');
const percentText = (value:number|null) => value===null?'—':`${Math.round(value*100)}%`;
/** States the result itself: not checked, checking a/t, precision against this check's own target, or check again. */
export function qualityText(value:Validation|null,result?:QualityResult) {
  if(!value)return t('wUnevaluated');
  if(!value.applicable)return t('wStale');
  const count=result?.precision_complete===false?result.precision_sample:value.stage!=='complete'||!result?value.counts[validationPhase(value)]:null;
  if(count)return value.stage==='calibration'&&!count.answered?t('wUnevaluated'):fill('wReviewing',{answered:count.answered,total:count.total});
  // Goal met uses the one-sided 95% lower bound, so a point estimate above the target can still fall short.
  const precision=result!.precision.value;
  const key=result!.goal_met?'wGoalMetDynamic':precision!==null&&precision>=value.target_precision?'wGoalUnsureDynamic':'wGoalNotMetDynamic';
  return fill(key,{p:percentText(precision),target:percentText(value.target_precision)});
}
/** A finished check where no AI reached its goal: the species needs another way to be confirmed. */
export const goalMissed = (value:Validation|null) => !!value?.applicable&&value.stage==='complete'&&Object.keys(value.results).length>0&&!Object.values(value.results).some(result=>result.goal_met);
// Missed calls are advisory (user decision 2026-09-25): below this share of calls found, the check suggests other ways
// to record calls but the report group still follows the precision goal alone.
export const RECALL_GUIDE = .8;
/** A finished missed-call check where no AI found at least RECALL_GUIDE of the calls present. */
export const recallLow = (value:Validation|null) => {
  const checked=value?.applicable&&value.stage==='complete'?Object.values(value.results).filter(result=>result.recall_complete&&result.recall.value!==null):[];
  return checked.length>0&&checked.every(result=>result.recall.value!<RECALL_GUIDE);
};
/** Whether rebuilding the AI with this site's sounds can actually be done now, or only needs calls marked on "heard" sounds. */
export async function canRebuild(datasetId:number,speciesId:string,start_date:string,end_date:string) {
  try{const session=await api<{id:number}>('/annotations',{dataset_id:datasetId,start_date,end_date});
    const ready=await api<CustomModelReadiness>(`/annotations/${session.id}/custom-model-readiness?species_id=${encodeURIComponent(speciesId)}`);
    return ready.can_train||ready.needs_range.length>0&&ready.reasons.every(reason=>reason==='custom_positive_examples_needed');}
  catch{return false;}
}
const recallText = (result:QualityResult) => result.recall_complete&&result.recall.value!==null
  ?' · '+fill(result.recall.value<RECALL_GUIDE?'wRecallBelow':'wRecallMet',{p:percentText(result.recall.value),target:percentText(RECALL_GUIDE)}):'';
/** The one button into listening: start, continue (answered/total), see the result, or check again. */
export function listenLabel(value:Validation|null,start='wEvaluate') {
  if(!value||value.applicable&&value.stage==='calibration'&&!value.counts.calibration.answered)return t(start);
  if(!value.applicable)return t('wCheckAgain');
  if(value.stage==='complete')return t('wValidationDone');
  const count=value.counts[validationPhase(value)];
  return fill('vListenContinue',{answered:count.answered,total:count.total});
}
/** One line per AI once the check is complete; otherwise a single status line. */
export function qualityLines(value:Validation|null) {
  const results=value?.applicable&&value.stage==='complete'?Object.entries(value.results):[];
  return results.length?results.map(([engine,result])=>`${results.length>1?`${modelName(engine)}: `:''}${qualityText(value,result)}${recallText(result)}`):[qualityText(value)];
}
export const interval = (value:Metric) => value.low===null||value.high===null?'—':`${(value.low*100).toFixed(1)}–${(value.high*100).toFixed(1)}%`;
export const rangeQuery = (first:string,last:string) => new URLSearchParams({start_date:first,end_date:last}).toString();
// Below this many 15-second detections (by any AI), a species is more likely an AI mistake than present (review A2).
export const FEW_DETECTIONS = 10;
/** Site list sections: confirmed (a complete check of an AI or of collected similar calls met its goal), needs checking, or few detections and never checked. */
export function speciesGroup(row:Overview['species'][number]):'confirmed'|'check'|'few' {
  const checks=[row.validation,...(row.custom_models??[]).map(model=>model.validation),...row.searches.map(run=>run.validation??null)].filter(value=>value!==null);
  if(checks.some(value=>value.applicable&&value.stage==='complete'&&Object.values(value.results).some(result=>result.goal_met)))return 'confirmed';
  const most=Math.max(0,...row.models.map(model=>model.candidate_windows),...(row.custom_models??[]).map(model=>model.candidate_windows??0));
  return !checks.length&&!row.searches.length&&!row.annotated&&most<FEW_DETECTIONS?'few':'check';
}
// `return=` carries the in-app page to go back to (hash routing); anything not matching `allowed` is ignored.
export const returnPath = (url:URL,allowed:RegExp) => {const path=new URL(url.hash.slice(1)||url.pathname+url.search,url).searchParams.get('return')??'';return allowed.test(path)?path:'';};
export const appHref = (path:string) => `${resolve('/')}${path.slice(1)}`;
