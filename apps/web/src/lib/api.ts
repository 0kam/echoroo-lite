import { invoke, isTauri } from '@tauri-apps/api/core';
export type Connection = { url: string; token: string };
export let connection: Connection;
export async function connect() {
  if (isTauri()) connection = await invoke<Connection>('connection');
  else {
    const hash = new URLSearchParams(location.hash.slice(1));
    if (hash.has('url') && hash.has('token')) {
      sessionStorage.setItem('echoroo-dev', JSON.stringify({ url: hash.get('url'), token: hash.get('token') }));
      history.replaceState(null, '', location.pathname);
    }
    connection = JSON.parse(sessionStorage.getItem('echoroo-dev') || 'null');
    if (!connection || !/^http:\/\/127\.0\.0\.1:\d+$/.test(connection.url)) throw Error('disconnected');
  }
  await api('/health');
}
export async function api<T>(path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(connection.url + path, {
      method: body === undefined ? 'GET' : 'POST',
      headers: { Authorization: `Bearer ${connection.token}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch { throw Error('disconnected'); }
  if (!response.ok) throw Error((await response.json()).detail || 'unknown_error');
  return response.json();
}
export function audioURL(id: number) {
  return `${connection.url}/recordings/${id}/audio?token=${encodeURIComponent(connection.token)}`;
}
export function sourceAudioURL(kind: string, id: number) {
  return `${connection.url}/sources/${kind}/${id}/audio?token=${encodeURIComponent(connection.token)}`;
}
