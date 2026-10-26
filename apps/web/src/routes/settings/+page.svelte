<script lang="ts">
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t,type Model } from '$lib/app.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  import { appHref,returnPath } from '$lib/workflow';
  import SpeciesNames from '$lib/SpeciesNames.svelte';
  import XenoCantoKey from '$lib/XenoCantoKey.svelte';
  import Button from '$lib/ui/Button.svelte';
  // Shown once an AI was added: only that AI still has to run on recordings already analysed.
  let modelsChanged=$state(false);
  // Saved on each change; the box shows the new state at once and turns back if saving fails.
  const saveModel=createTask(async(model:Model,on:boolean)=>{
    model.enabled=on;
    try{app.models=await api<Model[]>('/models',{enabled:app.models.filter(item=>item.enabled).map(item=>item.id)},'PUT');modelsChanged||=on;}
    catch(e){model.enabled=!on;throw e;}
  });
  // Pages that link here for a missing Xeno-Canto key pass themselves as `return=`.
  const origin=$derived(returnPath(page.url,/^\/(datasets\/\d+\/(search|validation\/\d+|species\/[^/?#]+\/presence)|quiz\/add)(\?[^#]*)?$/));
</script>

<div class="page narrow">
  {#if origin}<div class="crumbs"><a href={appHref(origin)}>{t('wReturn')}</a></div>{/if}
  <div class="page-header"><div><h1>{t('navSettings')}</h1><p>{t('settingsIntro')}</p></div></div>
  <section class="panel" aria-labelledby="models-title">
    <h2 id="models-title">{t('modelsTitle')}</h2>
    <p class="muted">{t('modelsHint')}</p>
    {#each app.models.filter(model=>model.available) as model (model.id)}
      <label class="checkbox"><input type="checkbox" checked={model.enabled} disabled={model.required||saveModel.pending} onchange={e=>saveModel.run(model,e.currentTarget.checked)} />
        <span><strong>{model.name}</strong>{#if model.experimental} <span class="rare">{t('modelPreview')}</span>{/if}<br /><span class="muted">{t(`modelHint_${model.id}`)}</span></span></label>
    {/each}
    {#if saveModel.pending}<p class="muted" role="status">{t('modelsSaving')}</p>{:else if modelsChanged}<p class="info" role="status">{t('modelsChanged')}</p>{/if}
    {#if saveModel.error}<p class="error" role="alert">{t(saveModel.error)}</p>{/if}
  </section>
  <SpeciesNames />
  <section class="panel"><h2>{t('xenoCanto')}</h2><XenoCantoKey />{#if origin}<p><Button href={appHref(origin)}>{t('wReturn')}</Button></p>{/if}</section>
  <section class="panel" aria-labelledby="licenses-title">
    <h2 id="licenses-title">{t('licenses')}</h2>
    <p class="notice"><strong>{t('licenseNonCommercial')}</strong></p>
    <p>{t('licensePerch')}</p>
    <p>{t('licenseOsj')}</p>
    <p>{t('licenseWikidata')}</p>
    <p>{t('licenseBirdnet')}</p>
    <p>{t('licenseKinglet')}</p>
  </section>
</div>

<style>
  .checkbox { align-items: flex-start; margin-top: 12px; }
</style>
