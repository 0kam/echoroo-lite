<script lang="ts">
  import { interval } from './workflow';
  import { commonName } from './species';
  import { percent, type Cell, type Report } from './reports';
  import { app, t } from './app.svelte';
  let { report, onselect }: { report: Report; onselect: (cell: Cell) => void } = $props();
  let element = $state<SVGSVGElement>(null!), zoom = $state(100), focusIndex = $state(0);
  const uid = $props.id();
  const days = $derived(Math.round((Date.parse(report.end_date) - Date.parse(report.start_date)) / 86400000) + 1);
  const searchPeriod = $derived(report.search_snapshot?.start_date || report.search_snapshot?.end_date
    ? `${t('searchRecordingDates')}: ${report.search_snapshot.start_date ?? t('noDateLimit')}–${report.search_snapshot.end_date ?? t('noDateLimit')}` : '');
  const siteLines = $derived([`${report.site.name} · ${report.dataset.name}`, ...(searchPeriod ? [searchPeriod] : [])]
    .flatMap(line => Array.from(line).join('').match(new RegExp(`.{1,${app.locale === 'ja' ? 48 : 90}}`, 'gu')) || []));
  const header = $derived(132 + siteLines.length * 20);
  const cx = 480, outer = 330, inner = 60;
  const quality = $derived(report.kind==='detection' && report.validation?.applicable && report.validation.stage==='complete' ? report.validation.results[report.engine??''] : null);
  const cy = $derived(header + 360), height = $derived(header + 900);
  const ring = $derived((outer - inner) / days);
  const dateTicks = $derived([...new Set([0,1,2,3].map(i => Math.round((days-1)*i/3)))]);
  const metric = $derived(t(report.kind === 'search' ? 'maximumSimilarity' : 'candidateRate'));
  const scaleMin = $derived(report.kind === 'search' ? report.threshold ?? -1 : 0);
  const condition = $derived(report.kind === 'search' ? `${t('searchHistory')} #${report.run_id}` : `${report.engine === 'birdnet' ? 'BirdNET' : 'Perch'} · ${report.model_sha256.slice(0, 12)}`);
  const color = (cell: Cell) => {
    if (cell.status === 'no_recording') return 'transparent';
    if (cell.status === 'unanalysed' || cell.status === 'not_evaluated') return `url(#${uid}-missing)`;
    const value = report.kind === 'search' ? cell.max_similarity : cell.candidate_rate;
    if (value === null) return '#f1f0f2';
    if (value < scaleMin) return '#e0dfe4';
    const fraction = scaleMin === 1 ? 1 : Math.min(1, Math.max(0, (value - scaleMin) / (1 - scaleMin)));
    return `rgb(${[248, 224, 222].map((start, i) => Math.round(start + ([160,80,76][i] - start) * fraction)).join(',')})`;
  };
  function point(radius: number, hour: number) {
    const angle = hour / 24 * 2 * Math.PI - Math.PI / 2;
    return [cx + radius * Math.cos(angle), cy + radius * Math.sin(angle)];
  }
  // Geometry follows Echoroo full's PolarHeatmap; gaps scale down for 365/366 rings.
  function wedge(index: number, hour: number) {
    const gap = ring < 2 ? 0 : Math.min(.6, ring * .1), r0 = inner + index * ring + gap / 2, r1 = inner + (index + 1) * ring - gap / 2;
    return `M${point(r0,hour)} A${r0},${r0} 0 0 1 ${point(r0,hour+1)} L${point(r1,hour+1)} A${r1},${r1} 0 0 0 ${point(r1,hour)} Z`;
  }
  function describe(cell: Cell) {
    const value = report.kind === 'search' ? cell.max_similarity?.toFixed(3) ?? '—' : percent(cell.candidate_rate);
    return `${cell.date} ${cell.hour}:00 · ${t(`cell_${cell.status}`)} · ${metric}: ${value} · ${t('eligibleWindows')}: ${cell.eligible_windows} · ${t('confirmedEvidence')}: ${cell.confirmed_windows}`;
  }
  function navigate(event: KeyboardEvent, index: number) {
    const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 24, ArrowUp: -24 }[event.key];
    if (delta !== undefined) {
      event.preventDefault(); focusIndex = Math.min(report.cells.length - 1, Math.max(0, index + delta));
      element.querySelector<SVGElement>(`[data-cell="${focusIndex}"]`)?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onselect(report.cells[index]); }
  }
  export async function images() {
    const copy = element.cloneNode(true) as SVGSVGElement;
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('width', '2400'); copy.setAttribute('height', String(height * 2.5));
    copy.removeAttribute('style'); copy.setAttribute('font-family', 'system-ui, Yu Gothic, Hiragino Kaku Gothic ProN, sans-serif');
    copy.querySelectorAll('[tabindex]').forEach(node => { node.removeAttribute('tabindex'); node.removeAttribute('role'); });
    const svg = new XMLSerializer().serializeToString(copy);
    const image = new Image();
    image.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svg);
    await image.decode();
    const canvas = document.createElement('canvas'); canvas.width = 2400; canvas.height = Math.round(height * 2.5);
    canvas.getContext('2d')!.drawImage(image, 0, 0, canvas.width, canvas.height);
    return { svg, png: canvas.toDataURL('image/png').split(',')[1] };
  }
</script>

<div class="plot-controls"><label>{t('plotZoom')}<select bind:value={zoom}>{#each [100,150,200] as value}<option value={value}>{value}%</option>{/each}</select></label><span class="muted">{t('plotNavigation')}</span></div>
<div class="plot-scroll">
<svg bind:this={element} xmlns="http://www.w3.org/2000/svg" viewBox={`0 0 960 ${height}`} style:width={`${zoom}%`} style:max-width={`${720*zoom/100}px`}
  role="group" aria-label={t('spiralPlot')} font-family="system-ui, Yu Gothic, Hiragino Kaku Gothic ProN, sans-serif">
  <title>{t('spiralPlot')} · {report.species ? commonName(report.species, app.locale) : ''} · {metric}</title>
  <desc>{t('spiralMeaning')} {report.site.timezone} · {condition} · {report.start_date}–{report.end_date} · {searchPeriod}</desc>
  <defs><pattern id={`${uid}-missing`} width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#fafafa"/><path d="M-2,2 L2,-2 M0,7 L7,0 M5,9 L9,5" stroke="#aeacb8" stroke-width="1"/></pattern>
    <pattern id={`${uid}-partial`} width="9" height="9" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1" fill="#494454"/></pattern>
    <linearGradient id={`${uid}-gradient`}><stop offset="0%" stop-color="#f8e0de"/><stop offset="100%" stop-color="#a0504c"/></linearGradient>
  </defs>
  <rect width="960" height={height} fill="#fff"/>
  <text x="36" y="38" font-size="26" font-weight="600" fill="#373450">{report.species ? commonName(report.species, app.locale) || t('commonNameMissing') : ''}</text>
  <text x="36" y="67" font-size="20" font-style="italic" fill="#575279">{report.species?.scientific_name}</text>
  {#each siteLines as line, i}<text x="36" y={96+i*20} font-size="16" fill="#575279">{line}</text>{/each}
  <text x="36" y={header-22} font-size="15" fill="#575279">{report.start_date}–{report.end_date} · {report.site.timezone} · {condition}</text>
  <text x="36" y={header} font-size="15" fill="#575279">{metric} · {t('threshold')}: {report.threshold?.toFixed(3) ?? t('noThreshold')} · {report.window_s} {t('seconds')} {report.id ? ` · #${report.id}` : ''}</text>
  <circle {cx} {cy} r={outer} fill="#f1f0f2"/><circle {cx} {cy} r={inner} fill="#fff"/>
  {#each report.cells as cell, index}
    <path d={wedge(Math.floor(index/24),cell.hour)} fill={color(cell)} role="button" tabindex={focusIndex===index ? 0 : -1} data-cell={index}
      aria-label={describe(cell)} onclick={() => onselect(cell)} onkeydown={event => navigate(event,index)} onfocus={() => focusIndex=index}>
      <title>{describe(cell)}</title></path>
    {#if cell.status === 'partial'}<path d={wedge(Math.floor(index/24),cell.hour)} fill={`url(#${uid}-partial)`} pointer-events="none"/>{/if}
    {#if cell.confirmed_windows > 0}{@const p = point(inner+(Math.floor(index/24)+.5)*ring, cell.hour+.5)}
      <circle cx={p[0]} cy={p[1]} r={Math.max(1.4,Math.min(4,ring*.35))} fill="#222" stroke="#fff" stroke-width=".7" pointer-events="none"/>{/if}
  {/each}
  {#each [0,3,6,9,12,15,18,21] as hour}{@const p=point(outer+28,hour)}<text x={p[0]} y={p[1]+5} text-anchor="middle" font-size="17" fill="#575279">{String(hour).padStart(2,'0')}:00</text>{/each}
  {#if days>1}{#each dateTicks as day}{@const p=point(inner+(day+.5)*ring,16.5)}
    <circle cx={p[0]} cy={p[1]} r="1.5" fill="#575279" pointer-events="none"/>
    <text x={p[0]-7} y={p[1]-3} text-anchor="end" font-size="12" fill="#575279" stroke="#fff" stroke-width="3" paint-order="stroke" pointer-events="none">{new Date(Date.parse(report.start_date)+day*86400000).toISOString().slice(0,10)}</text>
  {/each}{/if}
  <text x={cx} y={cy-8} text-anchor="middle" font-size="13" fill="#575279">{t('dateOutward')}</text>
  <text x={cx} y={cy+15} text-anchor="middle" font-size="13" fill="#575279">{days} {t('days')}</text>
  <text x="70" y={cy+395} font-size="16" fill="#373450">{metric}</text>
  <rect x="370" y={cy+379} width="300" height="18" fill={`url(#${uid}-gradient)`}/>
  <text x="362" y={cy+395} text-anchor="end" font-size="14" fill="#575279">{report.kind==='search' ? scaleMin.toFixed(2) : '0%'}</text>
  <text x="682" y={cy+395} font-size="14" fill="#575279">{report.kind==='search' ? '1.00' : '100%'}</text>
  {#each [{fill:'#f1f0f2',key:'cell_no_recording'},{fill:`url(#${uid}-missing)`,key:'missingAnalysis'},
    {fill:`url(#${uid}-partial)`,key:'cell_partial'}] as item,i}
    <rect x={70+i*285} y={cy+419} width="20" height="16" fill={item.fill} stroke="#aeacb8" stroke-width=".5"/>
    <text x={100+i*285} y={cy+432} font-size="14" fill="#575279">{t(item.key)}</text>{/each}
  {#if report.kind==='search'}
    <rect x="70" y={cy+450} width="20" height="16" fill="#e0dfe4"/><text x="100" y={cy+463} font-size="14" fill="#575279">{t('belowThreshold')}</text>
    <circle cx="363" cy={cy+458} r="4" fill="#222"/><text x="382" y={cy+463} font-size="14" fill="#575279">{t('confirmedEvidence')}</text>
  {/if}
  <text x="70" y={cy+494} font-size="14" fill="#575279">{t(report.kind==='search' ? 'searchFigureCaution' : quality ? 'wFigureCaution' : 'detectionFigureCaution')}</text>
{#if quality}<text x="70" y={cy+523} font-size="14" fill="#575279">{t('wFigureQuality')} #{report.validation?.id} · precision {percent(quality.precision.value)} [{interval(quality.precision)}] · recall {percent(quality.recall.value)} [{interval(quality.recall)}]</text>{/if}
</svg>
</div>

<style>
  .plot-controls { display: flex; align-items: end; gap: 16px; margin: 10px 0; }
  .plot-controls select { width: 100px; }
  .plot-scroll { overflow: auto; max-height: 780px; border: 1px solid rgb(var(--stone-300)); background: #fff; }
  svg { display: block; height: auto; min-width: 600px; margin: 0 auto; }
  path[role=button] { cursor: pointer; }
  path[role=button]:focus { outline: none; stroke: #286983; stroke-width: 2px; }
</style>
