<script lang="ts">
  import { resolve } from '$app/paths';
  import { app, t } from '$lib/app.svelte';
  import Button from '$lib/ui/Button.svelte';
  import JobStatus from '$lib/JobStatus.svelte';
</script>

<div class="page">
  <div class="page-header">
    <div><h1>{t('navLibrary')}</h1><p>{t('libraryIntro')}</p></div>
    <Button variant="primary" href={resolve('/datasets/new')}>＋ {t('addDataset')}</Button>
  </div>
  {#if !app.sites.length}
    <div class="panel empty">
      <p>{t('noData')}</p>
      <Button variant="primary" size="lg" href={resolve('/datasets/new')}>＋ {t('addDataset')}</Button>
    </div>
  {:else}
    <div class="two-col">
      <div class="stack">
        {#each app.sites as site (site.id)}
          <section class="panel">
            <div class="section-heading">
              <div><h2>{site.name}</h2><p class="muted">{site.timezone} · {site.latitude}, {site.longitude}</p></div>
              <span class="muted">{app.datasets.filter(d => d.site_id === site.id).length} {t('datasetsCount')}</span>
            </div>
            <div class="list">
              {#each app.datasets.filter(d => d.site_id === site.id) as dataset (dataset.id)}
                <div class="list-item">
                  <div class="text"><strong>{dataset.name}</strong><div class="muted">{dataset.folder}</div></div>
                  <Button href={resolve(`/datasets/${dataset.id}`)} size="sm" active={dataset.id === app.datasetId}>{t('open')}</Button>
                </div>
              {/each}
            </div>
          </section>
        {/each}
      </div>
      <aside class="panel">
        <h2>{t('analysisStatus')}</h2>
        {#if !app.jobs.length}<p class="muted">{t('noJobs')}</p>{/if}
        {#each app.jobs.slice(0, 6) as job (job.id)}<JobStatus {job} showDataset />{/each}
      </aside>
    </div>
  {/if}
</div>

<style>
  .text { min-width: 0; }
  .text .muted { overflow-wrap: anywhere; }
</style>
