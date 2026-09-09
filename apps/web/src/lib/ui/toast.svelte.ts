export type Toast = { id: number; key: string; kind: 'success' | 'error' | 'info'; detail?: string };
export const toasts = $state<Toast[]>([]);
let next = 1;
/** Shows a short message; `key` is a message key, `detail` is shown verbatim. */
export function notify(key: string, kind: Toast['kind'] = 'success', detail?: string) {
  const id = next++;
  toasts.push({ id, key, kind, detail });
  setTimeout(() => dismiss(id), kind === 'error' ? 8000 : 4000);
}
export function dismiss(id: number) {
  const index = toasts.findIndex(toast => toast.id === id);
  if (index >= 0) toasts.splice(index, 1);
}
