import { homeDir } from '@tauri-apps/api/path';
import { open } from '@tauri-apps/plugin-dialog';

export async function chooseDirectory() {
  return open({ directory: true, multiple: false, defaultPath: await homeDir() });
}
