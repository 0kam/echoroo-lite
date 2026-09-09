<script lang="ts">
  import { t } from '../app.svelte';
  import { toasts, dismiss } from './toast.svelte';
</script>

<div class="toasts" aria-live="polite">
  {#each toasts as toast (toast.id)}
    <div class={`toast ${toast.kind}`} role={toast.kind === 'error' ? 'alert' : 'status'}>
      <span>{t(toast.key)}{#if toast.detail}<br /><small>{toast.detail}</small>{/if}</span>
      <button class="close" onclick={() => dismiss(toast.id)} aria-label={t('close')}>×</button>
    </div>
  {/each}
</div>

<style>
  .toasts { position: fixed; right: 16px; bottom: 16px; z-index: 90; display: flex; flex-direction: column; gap: 8px; max-width: min(420px, calc(100vw - 32px)); }
  .toast { display: flex; align-items: flex-start; gap: 12px; padding: 12px 14px; border-radius: 8px; font-size: 13px;
    background: rgb(var(--stone-900)); color: rgb(var(--stone-50)); box-shadow: 0 8px 24px rgb(0 0 0 / .2); animation: rise .2s ease-out; }
  .toast.success { border-left: 4px solid rgb(var(--color-success)); }
  .toast.error { border-left: 4px solid rgb(var(--color-danger)); }
  .toast.info { border-left: 4px solid rgb(var(--primary-500)); }
  .toast small { opacity: .8; overflow-wrap: anywhere; }
  .close { margin-left: auto; border: 0; background: transparent; color: inherit; padding: 0 4px; font-size: 16px; line-height: 1; min-height: 0; }
  @keyframes rise { from { opacity: 0; transform: translateY(8px); } }
</style>
