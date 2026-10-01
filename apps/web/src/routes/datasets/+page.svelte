<script lang="ts">
  import { resolve } from '$app/paths';
  import { app,t } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
</script>
<div class="page">
  <div class="page-header"><div><h1>{t('uSites')}</h1><p>{t('uSitesHint')}</p></div>
    {#if app.sites.length}<Button href={resolve('/datasets/new')}>{t('uNewSite')}</Button>{/if}</div>
  {#if !app.sites.length}<section class="panel empty"><p>{t('uFirstSite')}</p><Button variant="primary" href={resolve('/datasets/new')}>{t('uNewSite')}</Button></section>{/if}
  {#each app.sites as site}
    {@const datasets=app.datasets.filter(d=>d.site_id===site.id).sort((a,b)=>a.id-b.id)}
    <section class="panel section-heading"><h2>{site.name}</h2><div class="actions">
      <Button href={`${resolve('/datasets/new')}?site=${site.id}`}>{t('uAddRecordings')}</Button>
      {#if datasets.length}<Button variant="primary" href={resolve(`/datasets/${datasets[0].id}`)}>{t('uSeeResults')}</Button>{/if}
      <Button variant="ghost" href={resolve(`/sites/${site.id}/manage`)}>{t('mManage')}</Button>
    </div></section>
  {/each}
</div>
