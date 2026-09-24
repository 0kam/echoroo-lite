<script lang="ts">
  import { resolve } from '$app/paths';
  import { api } from './api.svelte';
  import { t } from './app.svelte';
  import type { CustomModel,CustomModelReadiness } from './workflow';
  import Button from './ui/Button.svelte';
  import Spinner from './ui/Spinner.svelte';
  import { createTask } from './ui/task.svelte';
  let {datasetId,speciesId,startDate,endDate,oncreated}:{datasetId:number;speciesId:string;startDate:string;endDate:string;oncreated:(model:CustomModel)=>void}=$props();
  let readiness=$state<CustomModelReadiness|null>(null),open=$state(false),loading=$state(false),error=$state('');
  async function load(){
    loading=true;error='';
    try {
      const session=await api<{id:number}>('/annotations',{dataset_id:datasetId,start_date:startDate,end_date:endDate});
      readiness=await api<CustomModelReadiness>(`/annotations/${session.id}/custom-model-readiness?species_id=${encodeURIComponent(speciesId)}`);
    } catch(e) { error=e instanceof Error?e.message:'unknown_error'; }
    finally { loading=false; }
  }
  const create=createTask(async()=>{
    if(!readiness)return;
    const model=await api<CustomModel>('/custom-models',{annotation_session_id:readiness.annotation_session_id,species_id:speciesId});
    oncreated(model);
  },{toast:true});
  const searchHref=$derived(`${resolve(`/datasets/${datasetId}/search`)}?species=${encodeURIComponent(speciesId)}&start_date=${startDate}&end_date=${endDate}`);
</script>

{#if !open}<Button onclick={()=>{open=true;void load();}}>{t('customModelTitle')}</Button>
{:else}<section class="panel custom-model">
  <h2>{t('customModelTitle')}</h2><p>{t('customModelIntro')}</p>
  {#if loading}<p role="status"><Spinner/> {t('loading')}</p>
  {:else if readiness}
    <h3>{t('customModelMaterials')}</h3>
    <div class="stat-row"><span><strong>{readiness.examples.positive_5s}</strong> {t('customModelPositive')}</span><span><strong>{readiness.examples.negative_5s}</strong> {t('customModelNegative')}</span><span><strong>{readiness.examples.recordings}</strong> {t('customModelRecordings')}</span></div>
    {#if readiness.can_train}
      <p class="muted">{t('customModelEvaluationPending')}</p>
      <Button variant="primary" pending={create.pending} onclick={()=>create.run()}>{t('customModelCreate')}</Button>
      {#if create.pending}<p role="status"><Spinner/> {t('customModelPreparing')}</p>{/if}
    {:else}
      <p class="notice">{t('customModelNeedsMore')}</p>
      {#each readiness.reasons as reason}<p class="muted">{t(reason)}</p>{/each}
      <Button variant="primary" href={searchHref}>{t('customModelFindMore')}</Button>
    {/if}
  {/if}
  {#if error||create.error}<p class="error" role="alert">{t(error||create.error)}</p>{/if}
</section>
{/if}

<style>.custom-model h3{font-size:16px;margin:20px 0 8px}.custom-model .stat-row{gap:18px;flex-wrap:wrap;margin:12px 0 18px}.custom-model p{margin:12px 0}</style>
