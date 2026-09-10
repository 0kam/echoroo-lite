<script lang="ts">
  import { onMount } from 'svelte';
  import { t } from './app.svelte';
  import 'leaflet/dist/leaflet.css';
  let { latitude = $bindable<number|null>(null), longitude = $bindable<number|null>(null), editable = false }:
    {latitude?:number|null;longitude?:number|null;editable?:boolean} = $props();
  let container: HTMLDivElement;
  let map = $state<import('leaflet').Map|null>(null), marker: import('leaflet').CircleMarker;
  let failed = $state(false);
  const valid = () => latitude!==null && longitude!==null && Number.isFinite(latitude) && Number.isFinite(longitude) && Math.abs(latitude)<=90 && Math.abs(longitude)<=180;
  function choose(lat: number, lng: number) {
    if (!editable) return;
    latitude = +Math.max(-90,Math.min(90,lat)).toFixed(6);
    longitude = +(((lng+180)%360+360)%360-180).toFixed(6);
  }
  onMount(() => {
    let disposed = false, timer: ReturnType<typeof setTimeout>;
    void import('leaflet').then(L => {
      if (disposed) return;
      const center: [number,number] = valid() ? [latitude!,longitude!] : [35,138];
      // WKWebView can leave loaded tiles transparent during the fade; render them immediately.
      const view = L.map(container,{scrollWheelZoom:false,fadeAnimation:false}).setView(center,valid()?15:5);
      marker = L.circleMarker(center,{radius:8,color:'#fff',weight:3,fillColor:'#914c49',fillOpacity:1});
      timer = setTimeout(()=>failed=true,10000);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
        maxZoom:19,attribution:'&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>'
      }).on('tileerror',()=>failed=true).on('tileload',()=>{clearTimeout(timer);failed=false;}).addTo(view);
      view.on('click',event=>choose(event.latlng.lat,event.latlng.lng));
      map = view;
    }).catch(()=>failed=true);
    return () => {disposed=true;clearTimeout(timer);map?.remove();};
  });
  $effect(() => {
    if (!map) return;
    for (const [selector, label] of [['.leaflet-control-zoom-in',t('mZoomIn')],['.leaflet-control-zoom-out',t('mZoomOut')]]) {
      const button=container.querySelector(selector);
      button?.setAttribute('title',label);button?.setAttribute('aria-label',label);
    }
    if (valid()) {
      marker.setLatLng([latitude!,longitude!]).addTo(map);
      if (!map.getBounds().contains([latitude!,longitude!])) map.panTo([latitude!,longitude!]);
    } else marker.remove();
  });
</script>
<!-- Leaflet supplies keyboard panning and interactive controls inside this map application. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
<div bind:this={container} class="site-map" role="application" aria-label={t('mMap')} tabindex="0"
  onkeydown={event=>{if(event.key==='Enter'&&editable&&map){event.preventDefault();const p=map.getCenter();choose(p.lat,p.lng);}}}></div>
<p class="muted">{t(editable?'mMapHint':'mMapReadOnly')}</p>
{#if failed}<p class="notice" role="status">{t('mMapOffline')}</p>{/if}
<style>.site-map{height:300px;border-radius:8px;background:#ece9e3;z-index:0}.site-map :global(.leaflet-control-zoom a){width:44px;height:44px;line-height:44px;font-size:24px}p{margin:10px 0;font-size:14px}</style>
