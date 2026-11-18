import { app } from './app.svelte';
import { commonName, type Species } from './species';
import { fill } from './workflow';

export type Family = Species & { rank: 'family' };
export type RollupSummary = { start_date: string; end_date: string;
  families: { family: Family; sounds: number; left: number; guesses: Species[] }[] };
export type RollupAnswer = { kind: 'species' | 'family' | 'not_bird' | 'unknown'; species: Species | null };
export type RollupSound = { recording_id: number; start_s: number; end_s: number; family: string; engines: string[];
  source_path: string; seconds: number; local_time: string; guess: Species; japan_guess: Species | null; answer: RollupAnswer | null };
export type RollupFamily = { family: Family; site: { name: string; timezone: string }; start_date: string; end_date: string;
  choices: (Species & { likely: boolean })[]; sounds: RollupSound[] };

/** A species' name in the app's language, or its scientific name. */
export const speciesName = (species: Species) => commonName(species, app.locale) || species.scientific_name;
/** The family's own name ("キツツキ科"); without one in this language, its scientific name. */
export const familyName = (family: Species) => family.common_names[app.locale] || family.scientific_name;
/** "キツツキ科の一種". */
export const familyLabel = (family: Species) => fill('ruFamilyOf', { name: familyName(family) });
