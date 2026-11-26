import { app, t } from './app.svelte';
import { commonName, type Species } from './species';
import { fill } from './workflow';

export type Family = Species & { rank: 'family' };
/** `family` null: the last row, every family without a species in Japan together (`families` of them, the two largest in `examples`). */
export type RollupSummary = { pending?: number; start_date: string; end_date: string;
  families: { family: Family | null; sounds: number; left: number; guesses: Species[]; families?: number; examples?: Family[] }[] };
export type RollupAnswer = { kind: 'species' | 'family' | 'not_bird' | 'unknown'; species: Species | null };
export type RollupSound = { recording_id: number; start_s: number; end_s: number; family: string; engines: string[];
  source_path: string; seconds: number; local_time: string; guess: Species; japan_guess: Species | null; answer: RollupAnswer | null };
export type RollupFamily = { pending?: number; family: Family | null; site: { name: string; timezone: string }; start_date: string; end_date: string;
  choices: (Species & { likely: boolean })[]; sounds: RollupSound[] };

/** A species' name in the app's language, or its scientific name. */
export const speciesName = (species: Species) => commonName(species, app.locale) || species.scientific_name;
/** The family's own name ("キツツキ科"); without one in this language, its scientific name. */
export const familyName = (family: Species) => family.common_names[app.locale] || family.scientific_name;
/** "キツツキ科の一種": a person's answer that stops at the family. */
export const familyLabel = (family: Species) => fill('ruFamilyOf', { name: familyName(family) });
/** "AI の答えが キツツキ科 の鳥だった音": the AI's answer is not a finding, so its rows never read as "a species of". */
export const rollupTitle = (family: Species | null) => family ? fill('ruRow', { family: familyName(family) }) : t('ruNoneRow');
/** The page of the families without a species in Japan, in the family route. */
export const NO_JAPAN = '_none';
