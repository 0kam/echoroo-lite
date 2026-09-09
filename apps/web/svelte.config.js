import adapter from '@sveltejs/adapter-static';
import { vitePreprocess } from '@sveltejs/vite-plugin-svelte';
export default {
  preprocess: vitePreprocess(),
  // Hash routing keeps deep links and reloads working inside the Tauri asset protocol.
  kit: { adapter: adapter({ fallback: 'index.html' }), router: { type: 'hash' } },
};
