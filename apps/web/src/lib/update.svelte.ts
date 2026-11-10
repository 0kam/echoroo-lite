import { getVersion } from '@tauri-apps/api/app';
import { Channel, invoke, isTauri } from '@tauri-apps/api/core';

/** Where a new version can always be installed by hand. */
export const DOWNLOAD_PAGE = 'https://github.com/0kam/echoroo-lite/releases/latest';
export const releasePage = (version: string) => `https://github.com/0kam/echoroo-lite/releases/tag/v${version}`;

/** In-app update of the desktop app. In the plain browser nothing is ever checked and `current` stays empty. */
export const update = $state({
  /** Version of the running app. */
  current: '',
  /** Message key for the result of the last check; empty before the first one. */
  check: '' as '' | 'updChecking' | 'updLatest' | 'updCheckFailed',
  phase: 'none' as 'none' | 'available' | 'downloading' | 'ready' | 'installing' | 'failed',
  version: '',
  /** Download size in bytes, when the release states it. */
  size: null as number | null,
  done: 0, total: null as number | null,
  /** "Later" hides the strip until the next start; Settings still offers the update. */
  later: false,
});
let downloaded = false;

export async function checkForUpdate() {
  if (!isTauri()) return;
  update.check = 'updChecking';
  try {
    update.current ||= await getVersion();
    const found = await invoke<{ version: string; size: number | null } | null>('update_check');
    if (found) Object.assign(update, found, { phase: 'available' });
    update.check = found ? '' : 'updLatest';
  } catch { update.check = 'updCheckFailed'; }
}

/** Downloads the new version; the app is replaced only when the user then presses "Restart and update". */
export async function downloadUpdate() {
  Object.assign(update, { phase: 'downloading', later: false, done: 0, total: update.size });
  const progress = new Channel<[number, number | null]>();
  progress.onmessage = ([done, total]) => { update.done = done; update.total = total ?? update.size; };
  try { await invoke('update_download', { progress }); downloaded = true; update.phase = 'ready'; }
  catch { update.phase = 'failed'; }
}

export async function installUpdate() {
  update.phase = 'installing';
  // On success the app restarts, so there is nothing to do afterwards.
  try { await invoke('update_install'); } catch { update.phase = 'failed'; }
}

export const retryUpdate = () => downloaded ? installUpdate() : downloadUpdate();
