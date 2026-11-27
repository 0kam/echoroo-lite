<script lang="ts">
  import { resolve } from '$app/paths';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { api } from '$lib/api.svelte';
  import { app,t,datasetOf,siteOf } from '$lib/app.svelte';
  import { commonName,type Species } from '$lib/species';
  import { fill,pageQuery,versionName,withQuery,type AIVersions } from '$lib/workflow';
  import Button from '$lib/ui/Button.svelte';
  import Spinner from '$lib/ui/Spinner.svelte';
  import { createTask } from '$lib/ui/task.svelte';
  // The species' AIs on this PC as versions: what each grew from, where it is used, how it does on this site's test
  // sounds, and a way to use any of them here. A version grown from another sits under it.
  type Version = AIVersions['versions'][number];
  const id=Number(page.params.id),speciesId=page.params.species!;
  const site=$derived(siteOf(datasetOf(id)));
  const range=new URLSearchParams([...pageQuery(page.url).params].filter(([key])=>key.endsWith('_date')));
  let data=$state<AIVersions|null>(null),species=$state<Species|null>(null),error=$state('');
  const load=createTask(async()=>{data=await api<AIVersions>(`/datasets/${id}/species/${speciesId}/ai-versions?${range}`);});
  onMount(async()=>{species=await api<Species>(`/species/${encodeURIComponent(speciesId)}`).catch(()=>null);await load.run();if(load.error)error=load.error;});
  const use=createTask(async(modelId:number)=>{const dates=Object.fromEntries(range);await api(`/custom-models/${modelId}/use`,{dataset_id:id,...dates});await load.run();},{toast:true});
  const back=withQuery(resolve(`/datasets/${id}/species/${speciesId}/model`),range.toString());
  const day=(value?:string|null)=>value?new Intl.DateTimeFormat(app.locale,{dateStyle:'medium'}).format(new Date(`${value.replace(' ','T')}${value.includes('T')?'':'Z'}`)):'';
  // Children: the versions whose parent is this one (a file's AI has no link to a row of this PC).
  const roots=$derived(data?data.versions.filter(v=>!v.parent?.model_id||!data!.versions.some(w=>w.id===v.parent!.model_id)):[]);
  const children=(v:Version)=>data!.versions.filter(w=>w.parent?.model_id===v.id);
</script>
{#snippet version(v:Version)}<li>
  {#if v.file}<strong>{t('vlFile')}</strong> {fill(v.site?'vlFileFrom':'vlFileNoSite',{date:day(v.made_at),site:v.site??''})}
  {:else}<strong>{versionName(v)}</strong>{#if v.parent} {fill('vlFrom',{parent:versionName(v.parent)})}{/if}{/if}
  {#if v.here}<span class="pill">{t('vlUsedHere')}</span>{/if}
  {#each v.used.filter(u=>u.site_id!==site?.id) as u (u.site_id)}<span class="pill">{u.round===v.round?fill('vlUsedAt',{site:u.site}):fill('vlUsedAtRound',{site:u.site,n:u.round})}</span>{/each}
  <br><span class="muted">{v.score?fill('vlScore',v.score):t('vlNoScore')}{#if v.warning} {t('vlWarn')}{/if}</span>
  {#if !v.here}<div class="actions"><Button size="sm" pending={use.pending} onclick={()=>use.run(v.id)}>{t('vlUse')}</Button></div>{/if}
  {#if children(v).length}<ul>{#each children(v) as w (w.id)}{@render version(w)}{/each}</ul>{/if}
</li>{/snippet}
<div class="page">
  <div class="crumbs"><a href={back}>{t('wReturn')}</a></div>
  <div>{#if site}<div class="site-name">{site.name}</div>{/if}<h1>{species?fill('vlTitle',{name:commonName(species,app.locale)||species.scientific_name}):t('vlLink')}</h1></div>
  <p class="muted">{t('vlIntro')}</p>
  {#if error}<p class="error" role="alert">{t(error)}</p>{/if}
  {#if use.error}<p class="error" role="alert">{t(use.error)}</p>{/if}
  {#if data}<section class="panel tree">{#if data.versions.length}<ul>{#each roots as v (v.id)}{@render version(v)}{/each}</ul>{:else}<p>{t('vlNone')}</p>{/if}</section>
  {:else if !error}<p role="status"><Spinner/> {t('loading')}</p>{/if}
</div>
<style>.tree ul{list-style:none;margin:0;padding:0}.tree li{margin:0 0 14px}.tree li ul{margin:8px 0 0 6px;padding-left:1.4em;border-left:2px solid rgb(var(--stone-300))}
  .pill{display:inline-block;margin-left:6px;padding:0 8px;border:1px solid rgb(var(--primary-700));border-radius:999px;color:rgb(var(--primary-700));font-size:13px}
  .actions{margin-top:6px}</style>
