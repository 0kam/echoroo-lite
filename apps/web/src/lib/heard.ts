import { t } from './app.svelte';
import type { AudioSource } from './AudioRegion.svelte';
import { familyLabel, speciesName } from './rollups';
import type { Species } from './species';
import { fill } from './workflow';

export type Verdict = 'present' | 'absent' | 'unknown';
export type Taxon = Species & { rank?: 'family' };
export type Counts = Record<Verdict, number>;
export type AiState = { state: 'made'; round: number; usable: boolean } | { state: 'ready' | 'needed'; needed: number } | { state: 'family' };
export type HeardSummary = { rows: { species: Taxon; site: Counts; all: Counts; ai: AiState | null }[];
  total: { site: number; all: number }; requirements: { total: number; each: number } };
export type HeardRecord = { id: number; species: Taxon; verdict: Verdict; screen: string; extra: boolean; recording_id: number;
  start_s: number; end_s: number; created_at: string; local_time: string; source: AudioSource };
export type HeardList = { total: number; rows: HeardRecord[]; site: { name: string; timezone: string };
  options: { species: Taxon[]; screens: string[]; days: string[] } };
export type HeardSound = { file: string; local_time: string; start_s: number; end_s: number; source: AudioSource;
  now: HeardRecord[]; history: (HeardRecord & { current: boolean; current_id: number })[] };
export type Other = { species: Taxon; removable: boolean };
export type Choices = { species: (Taxon & { rare: boolean })[]; families: Taxon[] };
export type SearchExamples = { examples: HeardRecord[]; heard: number; mixed: number;
  mixed_example: { local_time: string; species: Taxon[] } | null };

/** A species' name, or "キツツキ科の一種" for an answer that stops at the family. */
export const taxonName = (taxon: Taxon) => taxon.rank === 'family' ? familyLabel(taxon) : speciesName(taxon);
/** "聞こえた" / "聞こえなかった" / "わからなかった". */
export const verdictText = (verdict: Verdict) => t(`hdV_${verdict}`);
/** The screen an answer was recorded on, in that screen's own name. */
export const screenName = (screen: string, extra = false) => {
  const name = t(['species_mark', 'validation', 'training', 'sound_map', 'rollup_fix', 'review'].includes(screen) ? `hdScreen_${screen}` : 'hdScreen_other');
  return extra && screen !== 'review' ? fill('hdScreenExtra', { screen: name }) : name;
};
