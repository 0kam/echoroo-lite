<script lang="ts">
  import { activeJob, t } from './app.svelte';
  import { openExternal } from './files';
  import { fill } from './workflow';
  import Button from './ui/Button.svelte';
  import { DOWNLOAD_PAGE, downloadUpdate, installUpdate, releasePage, retryUpdate, update } from './update.svelte';
  /** `strip`: the row under the top bar, with "Later". `page`: always offer the download page.
   * `quiet`: another element on the page already announces this state. */
  let { strip = false, page = false, quiet = false }: { strip?: boolean; page?: boolean; quiet?: boolean } = $props();
  const mb = (bytes: number) => `${Math.round(bytes / 1e6)} MB`;
  const phase = $derived(update.phase);
  // Never restart under a running analysis; the download waits too, so the two do not compete for memory.
  const busy = $derived(!!activeJob());
</script>

{#if phase !== 'none' || page}
  <div class="update" class:strip class:failed={phase === 'failed'} role={quiet ? undefined : phase === 'failed' ? 'alert' : 'status'}>
    {#if phase !== 'none'}
      <div class="text">
        {#if phase === 'available'}
          {fill('updAvailable', { version: update.version })}
          <a href={releasePage(update.version)} onclick={e => { e.preventDefault(); void openExternal(releasePage(update.version)); }}>{t('updNotes')}</a>
        {:else if phase === 'downloading'}
          <!-- The numbers change every megabyte; screen readers hear the start and the end, not each step. -->
          <span aria-live="off">{fill('updDownloading', { done: Math.round(update.done / 1e6), total: update.total ? mb(update.total) : '? MB' })}</span>
          <progress max={update.total} value={update.total ? update.done : undefined} aria-label={t('updProgress')}></progress>
          <small>{t('updKeepUsing')}</small>
        {:else}
          {t(phase === 'failed' ? 'updFailed' : 'updReady')}
        {/if}
        {#if busy && phase !== 'downloading'}<small>{t('updBusy')}</small>{/if}
      </div>
    {/if}
    <div class="actions">
      {#if phase === 'available'}
        <Button variant="primary" disabled={busy} onclick={downloadUpdate}>{update.size ? fill('updStart', { size: mb(update.size) }) : t('updStartNoSize')}</Button>
        {#if strip}<Button variant="ghost" onclick={() => update.later = true}>{t('updLater')}</Button>{/if}
      {:else if phase === 'ready' || phase === 'installing'}
        <Button variant="primary" disabled={busy} pending={phase === 'installing'} onclick={installUpdate}>{t('updRestart')}</Button>
      {:else if phase === 'failed'}
        <Button variant="primary" disabled={busy} onclick={retryUpdate}>{t('updRetry')}</Button>
      {/if}
      {#if phase === 'failed' || page}<Button onclick={() => void openExternal(DOWNLOAD_PAGE)}>{t('updOpenPage')}</Button>{/if}
    </div>
  </div>
{/if}

<style>
  .update { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; text-align: left; font-size: 16px; color: rgb(var(--stone-900)); }
  .strip { flex: none; padding: 8px 4%; background: rgb(var(--color-success-light)); border-bottom: 1px solid rgb(var(--color-success) / .35); }
  .strip.failed { background: rgb(var(--color-danger-light)); border-bottom-color: rgb(var(--color-danger) / .4); }
  .text { flex: 1 1 320px; min-width: 0; }
  .text a { font-size: 14px; }
  small { display: block; font-size: 14px; color: rgb(var(--stone-800)); }
  progress { display: block; margin-top: 4px; }
</style>
