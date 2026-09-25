import { isTauri } from '@tauri-apps/api/core';
import { homeDir } from '@tauri-apps/api/path';
import { open } from '@tauri-apps/plugin-dialog';
import { openUrl } from '@tauri-apps/plugin-opener';

export async function chooseDirectory() {
  return open({ directory: true, multiple: false, defaultPath: await homeDir() });
}

/** Opens a page in the system browser; the desktop app allows only the domains in its capability file. */
export async function openExternal(url: string) {
  if (url.startsWith('//')) url = 'https:' + url; // Xeno-Canto licence links omit the scheme.
  if (isTauri()) await openUrl(url);
  else window.open(url, '_blank', 'noopener');
}
