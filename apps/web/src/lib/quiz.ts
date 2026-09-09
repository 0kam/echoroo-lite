import type { AudioSource } from './AudioRegion.svelte';
import type { Species } from './species';
export type QuizClip = { id: number; reference_id: number; species_id: string; species: Species; seconds: number;
  enabled: number; source: AudioSource; metadata: { audio_file?: string; image_file?: string; note: string;
    source: string; sound_type: string; label_source: string; original_source?: AudioSource;
    credit: { recordist?: string; source_url?: string; license?: string; photographer?: string; image_license?: string };
    xeno_canto?: {id: number; recordist: string; license: string}; } };
export type QuizLibrary = { clips: QuizClip[]; sets: {id: number; name: string; bundled_key: string | null; clip_ids: number[]}[];
  sessions: {id: number; count: number; answered: number; created_at: string}[] };
export type QuizFeedback = {position: number; status: string; clip: QuizClip; answer: Species | null; answered_at: string};
export type QuizSession = {id: number; count: number; answered: number; complete: boolean; correct: number; unknown: number;
  question: {position: number; seconds: number; choices: Species[]; feedback: QuizFeedback | null} | null;
  review: QuizFeedback[]; per_species: {species: Species; correct: number; incorrect: number; unknown: number; total: number}[]};

export function quizContext(url: URL) {
  const params = new URL(url.hash.slice(1) || url.pathname + url.search, url).searchParams;
  const candidate = params.get('return') ?? '';
  const returnPath = /^\/datasets\/\d+\/(species\/[a-zA-Z0-9-]+|validation\/\d+)(\?[^#]*)?$/.test(candidate) ? candidate : '';
  const species = params.get('species') ?? '';
  const query = new URLSearchParams();
  if (returnPath) query.set('return', returnPath);
  if (species) query.set('species', species);
  return { returnPath, species, query: query.toString() };
}
