<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount,tick } from 'svelte';
  import { page } from '$app/state';
  import { isTauri } from '@tauri-apps/api/core';
  import { chooseDirectory } from '$lib/files';
  import { api } from '$lib/api.svelte';
  import { app,t } from '$lib/app.svelte';
  import { commonName } from '$lib/species';
  import { qualityStatus,rangeQuery,type Overview,type SpeciesResult } from '$lib/workflow';
  import type { Report } from '$lib/reports';
  import SpiralPlot from '$lib/SpiralPlot.svelte';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  const id=Number(page.params.id),query=new URL(page.url.hash.slice(1),page.url).searchParams;
  let overview=$state<Overview|null>(null),selected=$state<string[]>([]),error=$state(''),folder=$state(''),output=$state(''),progress=$state('');
  let history=$state<{id:number;path:string;created_at:string;results:{report_id:number;scientific_name:string;common_name_ja:string;common_name_en:string;engine:string}[]}[]>([]),figure=$state<Report|null>(null);
  let legacy=$state<{id:number;created_at:string;kind:string;species:{scientific_name:string}|null}[]>([]);
  let plot=$state<{images:()=>Promise<{svg:string;png:string}>}>(null!);
  onMount(()=>{void(async()=>{try{overview=await api<Overview>(`/datasets/${id}/overview?${query}`);selected=overview.species.map(row=>row.species.id);
    history=await api(`/datasets/${id}/exports`);const saved=new Set(history.flatMap(item=>item.results.map(r=>r.report_id)));legacy=(await api<typeof legacy>(`/reports?dataset_id=${id}`)).filter(item=>!saved.has(item.id));}catch(e){error=e instanceof Error?e.message:'unknown_error';}})();});
  const exportFiles=createTask(async()=>{
    if(!overview)return;output='';
    if(isTauri()){const path=await chooseDirectory();if(!path)return;folder=path;}
    const figures=[];
    for(let index=0;index<selected.length;index++){
      const sid=selected[index],row=overview.species.find(r=>r.species.id===sid)!;progress=`${index+1} / ${selected.length} · ${commonName(row.species,app.locale)}`;
      const targets=[...overview.models.map(model=>({engine:model.engine,run_id:null as number|null})),...row.searches.map(run=>({engine:'search',run_id:run.id}))];
      for(const target of targets){
        const params=new URLSearchParams({start_date:overview.start_date,end_date:overview.end_date});
        if(target.run_id)params.set('run_id',String(target.run_id));else params.set('engine',target.engine);
        const result=await api<SpeciesResult>(`/datasets/${id}/species/${sid}?${params}`);
        if(result.revision!==overview.revision)throw Error('workflow_changed');
        if(!result.report)continue;figure=result.report;await tick();
        const images=figure.cells.length?await plot.images():{svg:'',png:''};
        figures.push({species_id:sid,...target,...images});
      }
    }
    const result=await api<{path:string}>('/workflow-exports',{dataset_id:id,start_date:overview.start_date,end_date:overview.end_date,revision:overview.revision,
      species_ids:selected,figures,folder,locale:app.locale});output=result.path;figure=null;progress='';history=await api(`/datasets/${id}/exports`);
  });
</script>
<div class="page"><div class="crumbs"><a href={resolve(`/datasets/${id}`)}>{t('uBackResults')}</a><span>{overview?.site.name}</span></div><h1>{t('wExport')}</h1><p>{t('wExportIntro')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if overview}<p>{overview.site.name} · {overview.start_date}–{overview.end_date} · {overview.site.timezone}</p>
    <div class="two-col"><section class="panel"><h2>{t('wIncludeSpecies')}</h2>{#each overview.species as row}<label class="export-species"><input type="checkbox" bind:group={selected} value={row.species.id} disabled={exportFiles.pending}/>
      <span>{commonName(row.species,app.locale)}<i class="scientific-name">{row.species.scientific_name}</i></span><small>{t(qualityStatus(row.validation))}</small></label>{/each}</section>
    <section class="panel"><p>{t('wExportContents')}</p>
      {#if !isTauri()}<label>{t('exportFolder')}<input bind:value={folder}/></label>{/if}
      <Button variant="primary" pending={exportFiles.pending} disabled={!selected.length||(!isTauri()&&!folder)} onclick={()=>exportFiles.run()}>{t('wExport')}</Button>
      {#if progress&&exportFiles.pending}<p role="status"><Spinner/> {progress}</p>{/if}
      {#if exportFiles.error}<p class="error" role="alert">{t(exportFiles.error)}</p>{/if}
      {#if output}<p class="notice" role="status">{t('reportExported')}<br>{output}</p>{/if}
    </section></div>
    {#if history.length}<section class="panel"><h2>{t('wExportHistory')}</h2>{#each history as item}<details><summary>{item.created_at}</summary><p class="muted path">{item.path}</p>{#each item.results as saved}<p><a href={resolve(`/datasets/${id}/reports/${saved.report_id}`)}>{(app.locale==='ja'?saved.common_name_ja:saved.common_name_en)||saved.scientific_name} · <i>{saved.scientific_name}</i> · {saved.engine}</a></p>{/each}</details>{/each}</section>{/if}
    {#if legacy.length}<details><summary>{t('wEarlierReports')}</summary>{#each legacy as item}<p><a href={resolve(`/datasets/${id}/reports/${item.id}`)}>#{item.id} · {item.created_at} · {item.species?.scientific_name??t('species')}</a></p>{/each}</details>{/if}
  {:else if !error}<p><Spinner/> {t('loading')}</p>{/if}
  {#if figure}<div class="export-render" aria-hidden="true"><SpiralPlot report={figure} onselect={()=>{}} bind:this={plot}/></div>{/if}
</div>
<style>h1{margin-top:16px}p{margin:16px 0}.export-species{display:flex;align-items:center;gap:12px;padding:14px 0;border-bottom:1px solid rgb(var(--stone-300))}.export-species small{margin-left:auto}.export-species input{width:18px}.export-render{position:fixed;left:-10000px;top:0;width:960px;pointer-events:none}.path{overflow-wrap:anywhere}</style>
