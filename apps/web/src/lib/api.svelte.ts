import { invoke, isTauri } from '@tauri-apps/api/core';
import { untrack } from 'svelte';
export type Connection = { url: string; token: string };
export type Health = { status: 'ok' | 'data_incompatible'; data_folder: string; detail?: string | null };
export let connection: Connection;
/** Number of requests in flight; the activity bar reads it. */
export const activity = $state({ count: 0 });

export async function connect(): Promise<Health> {
  if (isTauri()) connection = await invoke<Connection>('connection');
  else {
    // Development: `?url=…&token=…` from the sidecar's first stdout line.
    const query = new URLSearchParams(location.search);
    if (query.has('url') && query.has('token')) {
      sessionStorage.setItem('echoroo-dev', JSON.stringify({ url: query.get('url'), token: query.get('token') }));
      history.replaceState(null, '', location.pathname + location.hash);
    }
    connection = JSON.parse(sessionStorage.getItem('echoroo-dev') || 'null');
    if (!connection || !/^http:\/\/127\.0\.0\.1:\d+$/.test(connection.url)) throw Error('disconnected');
  }
  return api<Health>('/health');
}
export async function api<T>(path: string, body?: unknown, method = body === undefined ? 'GET' : 'POST'): Promise<T> {
  untrack(() => activity.count++);
  try {
    let response: Response;
    try {
      response = await fetch(connection.url + path, {
        method,
        headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch { throw Error('disconnected'); }
    if (!response.ok) throw Error((await response.json()).detail || 'unknown_error');
    return response.json();
  } finally { untrack(() => activity.count--); }
}
export function audioURL(id: number) {
  return `${connection.url}/recordings/${id}/audio?token=${encodeURIComponent(connection.token)}`;
}
export function sourceAudioURL(kind: string, id: number) {
  return `${connection.url}/sources/${kind}/${id}/audio?token=${encodeURIComponent(connection.token)}`;
}
export function mediaURL(path: string) {
  return `${connection.url}${path}?token=${encodeURIComponent(connection.token)}`;
}
