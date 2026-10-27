import { api, connect } from './api.svelte';
import { en, ja, type Key } from './i18n';

export type Site = { id: number; name: string; timezone: string; latitude: number; longitude: number };
export type Dataset = { id: number; name: string; site_id: number; folder: string };
export type Job = { id: number; dataset_id: number; status: string; stage: string; done: number; total: number;
  error: string | null; created_at: string };
/** A detection AI from GET /models, in display order; the speeds are measured on this PC (null until measured). */
export type Model = { id: string; name: string; window_s: number; available: boolean; enabled: boolean; main_capable: boolean;
  experimental: boolean; default_threshold: number; species_count: number | null; license: string; seconds_per_hour: number | null; seconds_per_file: number | null };
/** GET/PUT /models: one main AI (also used for the similar-sound search) and optional AIs to compare.
 * `seconds_per_job` is the fixed start-up time of one analysis on this PC. */
export type ModelSettings = { main: string; extra: string[]; confirmed: boolean; seconds_per_job: number | null; models: Model[] };
export type Recording = { id: number; dataset_id: number; path: string; original_time: string; recorded_at: string;
  timezone: string; seconds: number };

type Locale = 'ja' | 'en';
export type FrequencyScale = 'mel' | 'linear';
const LOCALE = 'echoroo-locale', THEME = 'echoroo-theme', DATASET = 'echoroo-dataset', SCALE = 'echoroo-frequency-scale';

/** Global application state shared by every route. */
export const app = $state({
  locale: 'en' as Locale, dark: false,
  /** Frequency axis of every spectrogram; one choice for the whole app. */
  frequencyScale: 'mel' as FrequencyScale,
  connected: false, connectionError: '',
  /** Detection AIs; chosen on first run and in Settings. */
  ai: { main: 'perch', extra: [], confirmed: true, seconds_per_job: null, models: [] } as ModelSettings,
  dataFolder: '',
  sites: [] as Site[], datasets: [] as Dataset[], jobs: [] as Job[],
  datasetId: null as number | null,
  /** Bumps whenever the recordings of a dataset may have changed. */
  recordingsVersion: 0,
});

export function t(key: string): string {
  const table = app.locale === 'ja' ? ja : en;
  return table[key as Key] || en[key as Key] || table.unknown_error;
}

export const activeJob = () => app.jobs.find(job => job.status === 'queued' || job.status === 'running') ?? null;
export const datasetOf = (id: number | null) => app.datasets.find(d => d.id === id) ?? null;
export const siteOf = (dataset: Dataset | null) => app.sites.find(s => s.id === dataset?.site_id) ?? null;
export const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const fillText = (key: string, values: Record<string, number>) =>
  Object.entries(values).reduce((text, [name, value]) => text.replaceAll(`{${name}}`, value.toLocaleString(app.locale)), t(key));
/** Plain length such as "48 min" or "1 h 30 min"; never the ambiguous "48:00". */
export function duration(seconds: number) {
  if (seconds < 59.5) return fillText('durationS', { s: Math.round(seconds) });
  const minutes = Math.round(seconds / 60), h = Math.floor(minutes / 60), m = minutes % 60;
  return h ? fillText(m ? 'durationHM' : 'durationH', { h, m }) : fillText('durationM', { m });
}
/** A calendar date (YYYY-MM-DD) in the app's date style, e.g. 2025/01/01. */
export const day = (date: string) =>
  new Intl.DateTimeFormat(app.locale, { dateStyle: 'medium', timeZone: 'UTC' }).format(new Date(`${date.slice(0, 10)}T00:00:00Z`));
/** A date range that names a single day only once. */
export const dayRange = (first: string, last: string) => first === last ? day(first) : `${day(first)} – ${day(last)}`;
export const siteTime = (recording: { recorded_at: string; timezone: string }, style: 'short' | 'medium' = 'medium') =>
  new Intl.DateTimeFormat(app.locale, { dateStyle: style, timeStyle: 'short', timeZone: recording.timezone })
    .format(new Date(recording.recorded_at));

export function setLocale(locale: Locale) {
  app.locale = locale; document.documentElement.lang = locale;
  localStorage.setItem(LOCALE, locale);
}
export function setFrequencyScale(scale: FrequencyScale) {
  app.frequencyScale = scale;
  try { localStorage.setItem(SCALE, scale); } catch { /* The choice then lasts for this session only. */ }
}
export function setDark(dark: boolean) {
  app.dark = dark; document.documentElement.classList.toggle('dark', dark);
  localStorage.setItem(THEME, dark ? 'dark' : 'light');
}
export function rememberDataset(id: number | null) {
  app.datasetId = id;
  if (id === null) localStorage.removeItem(DATASET); else localStorage.setItem(DATASET, String(id));
}
export function restorePreferences() {
  const saved = localStorage.getItem(LOCALE);
  setLocale(saved === 'ja' || saved === 'en' ? saved : navigator.language.startsWith('ja') ? 'ja' : 'en');
  setDark(localStorage.getItem(THEME) === 'dark');
  app.frequencyScale = localStorage.getItem(SCALE) === 'linear' ? 'linear' : 'mel';
  const dataset = Number(localStorage.getItem(DATASET));
  app.datasetId = Number.isInteger(dataset) && dataset > 0 ? dataset : null;
}

export async function refreshCatalog() {
  const data = await api<{ sites: Site[]; datasets: Dataset[]; jobs: Job[] }>('/catalog');
  app.sites = data.sites; app.datasets = data.datasets; app.jobs = data.jobs;
  if (app.datasetId !== null && !data.datasets.some(d => d.id === app.datasetId)) rememberDataset(null);
}

/** Connects to the sidecar and keeps the catalogue fresh while the app is open. */
export function startSession() {
  let timer: ReturnType<typeof setInterval> | undefined, polling = false;
  (async () => {
    try {
      const health = await connect();
      app.dataFolder = health.data_folder;
      if (health.status !== 'ok') throw Error(health.detail || 'data_incompatible');
      app.ai = await api<ModelSettings>('/models');
      await refreshCatalog(); app.connected = true;
    }
    catch (e) { app.connectionError = e instanceof Error ? e.message : 'unknown_error'; return; }
    timer = setInterval(async () => {
      if (polling) return;
      polling = true;
      try { await refreshCatalog(); app.connectionError = ''; }
      catch (e) { app.connectionError = e instanceof Error ? e.message : 'unknown_error'; }
      finally { polling = false; }
    }, 2000);
  })();
  return () => clearInterval(timer);
}

const recordingCache = new Map<number, { version: number; rows: Recording[] }>();
/** Recordings of a dataset, cached until an import bumps the version. */
export async function loadRecordings(datasetId: number) {
  const cached = recordingCache.get(datasetId);
  if (cached && cached.version === app.recordingsVersion) return cached.rows;
  const rows = await api<Recording[]>(`/datasets/${datasetId}/recordings`);
  recordingCache.set(datasetId, { version: app.recordingsVersion, rows });
  return rows;
}
