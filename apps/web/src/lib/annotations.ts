import type { Species } from './species';
import type { AudioSource } from './AudioRegion.svelte';
export type CallRange = {species_id:string|null; start_s:number; end_s:number};
export type AnnotationSession = {
  id:number;dataset_id:number;site:{id:number;name:string;timezone:string};start_date:string;end_date:string;
  total:number;completed:number;palette:Species[];heard:{species:Species;intervals:number}[];
  history:{id:number;position:number}[];can_evaluate:boolean;has_holdout:boolean;applicable:boolean;
  question:{id:number;position:number;version:number;ranges:CallRange[];complete:boolean;source:AudioSource;
    start_s:number;end_s:number;local_time:string}|null;
};
