<script lang="ts">
  import { page } from '$app/state';
  import { t } from '$lib/app.svelte';
  import { appHref,returnPath } from '$lib/workflow';
  import SpeciesNames from '$lib/SpeciesNames.svelte';
  import XenoCantoKey from '$lib/XenoCantoKey.svelte';
  import Button from '$lib/ui/Button.svelte';
  // Pages that link here for a missing Xeno-Canto key pass themselves as `return=`.
  const origin=$derived(returnPath(page.url,/^\/(datasets\/\d+\/(search|validation\/\d+|species\/[^/?#]+\/presence)|quiz\/add)(\?[^#]*)?$/));
</script>

<div class="page narrow">
  {#if origin}<div class="crumbs"><a href={appHref(origin)}>{t('wReturn')}</a></div>{/if}
  <div class="page-header"><div><h1>{t('navSettings')}</h1><p>{t('settingsIntro')}</p></div></div>
  <SpeciesNames />
  <section class="panel"><h2>{t('xenoCanto')}</h2><XenoCantoKey />{#if origin}<p><Button href={appHref(origin)}>{t('wReturn')}</Button></p>{/if}</section>
  <section class="panel" aria-labelledby="licenses-title">
    <h2 id="licenses-title">{t('licenses')}</h2>
    <p class="notice"><strong>{t('licenseNonCommercial')}</strong></p>
    <p>{t('licensePerch')}</p>
    <p>{t('licenseOsj')}</p>
    <p>{t('licenseWikidata')}</p>
    <p>{t('licenseBirdnet')}</p>
  </section>
</div>
