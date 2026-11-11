<script lang="ts">
  import { page } from '$app/state';
  import { t } from '$lib/app.svelte';
  import { appHref,fill,returnPath } from '$lib/workflow';
  import UpdateControl from '$lib/UpdateControl.svelte';
  import { checkForUpdate, update } from '$lib/update.svelte';
  import ModelChoice from '$lib/ModelChoice.svelte';
  import SpeciesNames from '$lib/SpeciesNames.svelte';
  import XenoCantoKey from '$lib/XenoCantoKey.svelte';
  import Button from '$lib/ui/Button.svelte';
  // Pages that link here (a missing Xeno-Canto key, a faster AI for a long analysis) pass themselves as `return=`.
  const origin=$derived(returnPath(page.url,/^\/(datasets\/\d+(\/(search|validation\/\d+|species\/[^/?#]+\/presence))?|quiz\/add)(\?[^#]*)?$/));
</script>

<div class="page narrow">
  {#if origin}<div class="crumbs"><a href={appHref(origin)}>{t('wReturn')}</a></div>{/if}
  <div class="page-header"><div><h1>{t('navSettings')}</h1><p>{t('settingsIntro')}</p></div></div>
  <section class="panel" aria-labelledby="models-title">
    <h2 id="models-title">{t('modelsTitle')}</h2>
    <ModelChoice />
  </section>
  <SpeciesNames />
  <section class="panel"><h2>{t('xenoCanto')}</h2><XenoCantoKey />{#if origin}<p><Button href={appHref(origin)}>{t('wReturn')}</Button></p>{/if}</section>
  {#if update.current}
    <section class="panel stack" aria-labelledby="update-title">
      <h2 id="update-title">{t('updTitle')}</h2>
      <p>{fill('updCurrent', { version: update.current })}</p>
      {#if update.phase === 'none'}
        <div class="actions">
          <Button pending={update.check === 'updChecking'} onclick={checkForUpdate}>{t('updCheck')}</Button>
          <span class="muted" role="status">{update.check && t(update.check)}</span>
        </div>
      {:else}
        <!-- The strip under the top bar announces the same state unless "Later" has hidden it. -->
        <UpdateControl quiet={!update.later} />
      {/if}
    </section>
  {/if}
  <section class="panel" aria-labelledby="licenses-title">
    <h2 id="licenses-title">{t('licenses')}</h2>
    <p class="notice"><strong>{t('licenseNonCommercial')}</strong></p>
    <p>{t('licensePerch')}</p>
    <p>{t('licenseOsj')}</p>
    <p>{t('licenseWikidata')}</p>
    <p>{t('licenseBirdnet')}</p>
    <p>{t('licenseBirdnetV3')}</p>
    <p>{t('licenseKinglet')}</p>
  </section>
</div>

