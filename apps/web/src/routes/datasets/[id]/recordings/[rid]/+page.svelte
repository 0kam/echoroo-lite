<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { app,t,siteTime,loadRecordings,type Recording } from '$lib/app.svelte';
  import AudioRegion from '$lib/AudioRegion.svelte';
  import Button from '$lib/ui/Button.svelte';
  const id=Number(page.params.id),rid=Number(page.params.rid);
  let records=$state<Recording[]>([]),loading=$state(true),error=$state('');
  const index=$derived(records.findIndex(r=>r.id===rid)),record=$derived(records[index]);
  const previous=$derived(records[index-1]),next=$derived(records[index+1]);
  onMount(()=>{loadRecordings(id).then(rows=>records=rows).catch(e=>error=e.message).finally(()=>loading=false);});
</script>
<div class="page"><div class="crumbs"><a href={resolve(`/datasets/${id}/recordings`)}>{t('uBackRecordings')}</a></div>
  {#if error}<p class="error" role="alert">{t(error)}</p>{:else if loading}<p>{t('loading')}</p>{:else if record}
    <h1>{siteTime(record)}</h1>
    <section class="panel"><AudioRegion source={{...record,kind:'recording'}} selectable={false}/></section>
    <div class="actions between"><Button href={previous?resolve(`/datasets/${id}/recordings/${previous.id}`):undefined} disabled={!previous}>‹ {t('previousRecording')}</Button><Button href={next?resolve(`/datasets/${id}/recordings/${next.id}`):undefined} disabled={!next}>{t('nextRecording')} ›</Button></div>
  {:else}<p>{t('recordingNotFound')}</p>{/if}
</div>
<style>.between{justify-content:space-between}</style>
