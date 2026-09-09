import { notify } from './toast.svelte';
/** Wraps an async action with its own pending/error state so each button can show progress. */
export function createTask<A extends unknown[]>(fn: (...args: A) => Promise<void>, options: { toast?: boolean } = {}) {
  const task = $state({ pending: false, error: '' });
  return {
    get pending() { return task.pending; },
    get error() { return task.error; },
    set error(value: string) { task.error = value; },
    async run(...args: A) {
      if (task.pending) return; // Ignore repeated clicks while the first one is still running.
      task.pending = true; task.error = '';
      try { await fn(...args); }
      catch (e) {
        task.error = e instanceof Error ? e.message : 'unknown_error';
        if (options.toast) notify(task.error, 'error');
      }
      finally { task.pending = false; }
    },
  };
}
