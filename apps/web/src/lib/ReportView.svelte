<script lang="ts">
  import { api } from './api.svelte';
  import { app,t } from './app.svelte';
  import type { Cell,Report } from './reports';
  import SpiralPlot from './SpiralPlot.svelte';
  import AudioRegion from './AudioRegion.svelte';
  import Button from './ui/Button.svelte';
  let {report,liveUrl=''}:{report:Report;liveUrl?:string}=$props();
  let filtered=$state<Report|null>(null),filter=$state<Cell|null>(null),index=$state(0),loading=$state(false),error=$state('');
  let version=0;
  const visible=$derived(filtered??report),hit=$derived(visible.evidence[index]);
  async function select(cell:Cell|null){
    const current=++version;error='';filter=cell;index=0;
    if(!cell){filtered=null;loading=false;return;}
    loading=true;
    try{const response=await api<Report|{report:Report}>(liveUrl?`${liveUrl}&cell_date=${cell.date}&hour=${cell.hour}`:`/reports/${report.id}?cell_date=${cell.date}&hour=${cell.hour}`);
      if(current===version)filtered='report' in response?response.report:response;
    }catch(e){if(current===version)error=e instanceof Error?e.message:'unknown_error';}finally{if(current===version)loading=false;}
  }
</script>
{#if error}<p class="error" role="alert">{t(error)}</p>{/if}
{#if report.species}
  <div class="result-listen">
    <div><h2>{t('wSeason')}</h2><p>{t('uEvidenceHint')}</p>
      {#if report.cells.length}<SpiralPlot {report} compact onselect={select}/>{/if}
    </div>
    <section><h2>{t('evidenceAudio')}</h2>
      {#if filter}<p>{filter.date} · {filter.hour}:00 <Button onclick={()=>select(null)}>{t('uAllSounds')}</Button></p>{/if}
      {#if loading}<p role="status">{t('loading')}</p>
      {:else if hit}
        <p>{new Intl.DateTimeFormat(app.locale,{dateStyle:'medium',timeStyle:'short',timeZone:report.site.timezone}).format(new Date(hit.local_time))} · {index+1} / {visible.evidence.length}</p>
        {#key `${hit.recording_id}:${hit.start_s}`}<AudioRegion source={{id:hit.recording_id,kind:'recording',path:hit.path,seconds:hit.seconds}} fixedRange start={hit.start_s} end={hit.end_s}/>{/key}
        <div class="actions"><Button disabled={index===0} onclick={()=>index--}>‹ {t('uPreviousSound')}</Button><Button disabled={index>=visible.evidence.length-1} onclick={()=>index++}>{t('uNextSound')} ›</Button></div>
      {:else}<p class="empty">{t(filter?'uNoSoundHere':'uNoDetection')}</p>{/if}
    </section>
  </div>
{/if}
<style>.result-listen{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:28px}p{margin:12px 0}.actions{margin-top:20px;justify-content:space-between}@media(max-width:950px){.result-listen{grid-template-columns:1fr}}</style>
