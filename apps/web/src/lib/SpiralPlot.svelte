<script lang="ts">
  import { tick } from 'svelte';
  import { checkText,fill,modelName } from './workflow';
  import { commonName } from './species';
  import { percent, type Cell, type Report } from './reports';
  import { app, t } from './app.svelte';
  import Button from './ui/Button.svelte';
  // Without `onselect` the cells are a picture only (the species page has no sound list, B4).
  let { report, onselect, compact=false }: { compact?:boolean; report: Report; onselect?: (cell: Cell) => void } = $props();
  let element = $state<SVGSVGElement>(null!), focusIndex = $state(0), picked = $state('');
  const uid = $props.id();
  const days = $derived(Math.round((Date.parse(report.end_date) - Date.parse(report.start_date)) / 86400000) + 1);
  const monthName = (key: string, year = false) => new Intl.DateTimeFormat(app.locale, { timeZone: 'UTC', month: 'short', year: year ? 'numeric' : undefined })
    .format(new Date(`${key}-01T00:00:00Z`));
  // In a long period a ring is about a pixel thick and cannot be pressed, so the pressable plot can show one calendar month instead.
  // Months are read from the cells' site-local dates (one cell per day and hour, in date order): no timezone or locale can move a boundary.
  // The year is named on the month where it changes, and on the first month when it changes at all.
  const months = $derived.by(() => {
    const list: { key: string; first: number; days: number; newYear: boolean; name: string }[] = [];
    if (onselect && days > 62) for (let d = 0; d * 24 < report.cells.length; d++) {
      const key = report.cells[d * 24].date.slice(0, 7), last = list.at(-1);
      if (last?.key === key) last.days++;
      else list.push({ key, first: d, days: 1, newYear: !!last && last.key.slice(0, 4) !== key.slice(0, 4), name: '' });
    }
    const crossesYear = list.some(m => m.newYear);
    for (const [i, m] of list.entries()) m.name = monthName(m.key, m.newYear || (crossesYear && i === 0));
    return list;
  });
  // undefined = the whole period.
  const month = $derived(months.find(m => m.key === picked));
  const from = $derived(month?.first ?? 0), shown = $derived(month?.days ?? days);
  const cells = $derived(month ? report.cells.slice(from * 24, (from + shown) * 24) : report.cells);
  // The one cell in the tab order; it stays among the visible cells when the view changes.
  const tabStop = $derived(Math.min(from * 24 + cells.length - 1, Math.max(from * 24, focusIndex)));
  const siteLines = $derived([`${report.site.name}`]
    .flatMap(line => Array.from(line).join('').match(new RegExp(`.{1,${app.locale === 'ja' ? 48 : 90}}`, 'gu')) || []));
  const header = $derived(132 + siteLines.length * 20);
  const cx = 480, outer = 330, inner = 60;
  const quality = $derived(report.validation?.applicable ? report.validation : null);
  const cy = $derived(header + 360), height = $derived(header + 900);
  const ring = $derived((outer - inner) / shown);
  const dateTicks = $derived([...new Set([0,1,2,3].map(i => Math.round((shown-1)*i/3)))]);
  const metric = $derived(t('candidateRate'));
  const condition = $derived(`${modelName(report.engine)} · ${report.model_sha256.slice(0, 12)}`);
  const color = (cell: Cell) => {
    if (cell.status === 'no_recording') return 'transparent';
    if (cell.status === 'unanalysed' || cell.status === 'not_evaluated') return `url(#${uid}-missing)`;
    const value = cell.candidate_rate;
    if (value === null) return '#f1f0f2';
    if(value===0)return '#fff';
    const fraction = .25 + .75*Math.min(1, Math.max(0, value));
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
    const value = percent(cell.candidate_rate);
    return `${cell.date} ${cell.hour}:00 · ${t(`cell_${cell.status}`)} · ${metric}: ${value}`;
  }
  function navigate(event: KeyboardEvent, index: number) {
    const delta = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: 24, ArrowUp: -24 }[event.key];
    if (delta !== undefined) {
      event.preventDefault(); focusIndex = Math.min(from * 24 + cells.length - 1, Math.max(from * 24, index + delta));
      element.querySelector<SVGElement>(`[data-cell="${focusIndex}"]`)?.focus();
    } else if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); activate(report.cells[index], index); }
  }
  // In the whole-period view of a long period a cell opens its month; keyboard focus follows the cell into that view.
  async function activate(cell: Cell, index: number) {
    if (!months.length || month) return onselect?.(cell);
    picked = cell.date.slice(0, 7); focusIndex = index;
    await tick(); element.querySelector<SVGElement>(`[data-cell="${index}"]`)?.focus();
  }
  export async function images() {
    const copy = element.cloneNode(true) as SVGSVGElement;
    copy.setAttribute('xmlns', 'http://www.w3.org/2000/svg'); copy.setAttribute('width', '2400'); copy.setAttribute('height', String(height * 2.5));
    copy.removeAttribute('style'); copy.setAttribute('viewBox', `0 0 960 ${height}`); copy.setAttribute('font-family', getComputedStyle(document.documentElement).fontFamily);
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

{#if months.length}<div class="actions" role="group" aria-label={t('spiralPeriod')}>
  <Button size="sm" active={!month} aria-pressed={!month} onclick={() => picked = ''}>{t('spiralWhole')}</Button>
  {#each months as m (m.key)}<Button size="sm" active={m === month} aria-pressed={m === month} onclick={() => picked = m.key}>{m.name}</Button>{/each}
</div>{/if}
<div class="plot-scroll" class:compact>
<svg bind:this={element} xmlns="http://www.w3.org/2000/svg" viewBox={compact?`0 ${header-20} 960 755`:`0 0 960 ${height}`}
  role="group" aria-label={t(onselect ? 'spiralKeys' : 'spiralPlot')}>
  <title>{t('spiralPlot')} · {report.species ? commonName(report.species, app.locale) : ''} · {metric}</title>
  <desc>{t('spiralMeaning')} {report.site.timezone} · {report.start_date}–{report.end_date}</desc>
  <defs><pattern id={`${uid}-missing`} width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#fafafa"/><path d="M-2,2 L2,-2 M0,7 L7,0 M5,9 L9,5" stroke="#aeacb8" stroke-width="1"/></pattern>
    <pattern id={`${uid}-partial`} width="9" height="9" patternUnits="userSpaceOnUse"><circle cx="3" cy="3" r="1" fill="#494454"/></pattern>
    <linearGradient id={`${uid}-gradient`}><stop offset="0%" stop-color="#e2bcba"/><stop offset="100%" stop-color="#a0504c"/></linearGradient>
  </defs>
  <rect width="960" height={height} fill="#fff"/>
  <text visibility={compact?'hidden':undefined} x="36" y="38" font-size="26" font-weight="600" fill="#373450">{report.species ? commonName(report.species, app.locale) || t('commonNameMissing') : ''}</text>
  <text visibility={compact?'hidden':undefined} x="36" y="67" font-size="20" font-style="italic" fill="#575279">{report.species?.scientific_name}</text>
  {#each siteLines as line, i}<text visibility={compact?'hidden':undefined} x="36" y={96+i*20} font-size="16" fill="#575279">{line}</text>{/each}
  <text visibility={compact?'hidden':undefined} x="36" y={header-22} font-size="15" fill="#575279">{report.start_date}–{report.end_date} · {report.site.timezone} · {condition}</text>
  <text visibility={compact?'hidden':undefined} x="36" y={header} font-size="15" fill="#575279">{metric} · {t('threshold')}: {report.threshold == null ? t('noThreshold') : +report.threshold.toPrecision(2)} · {report.window_s} {t('seconds')} {report.id ? ` · #${report.id}` : ''}</text>
  <circle {cx} {cy} r={outer} fill="#f1f0f2"/><circle {cx} {cy} r={inner} fill="#fff"/>
  {#each cells as cell, i}{@const index=from*24+i}
    {#if onselect}<path d={wedge(Math.floor(i/24),cell.hour)} fill={color(cell)} role="button" tabindex={tabStop===index ? 0 : -1} data-cell={index}
      aria-label={describe(cell)} onclick={() => activate(cell,index)} onkeydown={event => navigate(event,index)} onfocus={() => focusIndex=index}>
      <title>{describe(cell)}</title></path>
    {:else}<path d={wedge(Math.floor(i/24),cell.hour)} fill={color(cell)}><title>{describe(cell)}</title></path>{/if}
    {#if cell.status === 'partial'}<path d={wedge(Math.floor(i/24),cell.hour)} fill={`url(#${uid}-partial)`} pointer-events="none"/>{/if}
  {/each}
  {#each [0,3,6,9,12,15,18,21] as hour}{@const p=point(outer+28,hour)}<text x={p[0]} y={p[1]+5} text-anchor="middle" font-size={compact?24:17} fill="#575279">{String(hour).padStart(2,'0')}:00</text>{/each}
  {#if months.length && !month}
    <!-- Whole period of a long recording: month boundaries instead of dates, heavier where the year changes. Names sit on a vertical line so that twelve months do not overlap. -->
    {#each months as m (m.key)}{@const r=inner+m.first*ring}{@const p=point(r+m.days*ring/2,12.5)}
      {#if m.first}<circle {cx} {cy} {r} fill="none" stroke={m.newYear?'#373450':'#8f8ba3'} stroke-width={m.newYear?3:1.4} pointer-events="none"/>{/if}
      {#if m.days*ring>=20}<text x={p[0]} y={p[1]+8} text-anchor="middle" font-size={compact?22:12} fill="#373450" stroke="#fff" stroke-width="3" paint-order="stroke" pointer-events="none">{monthName(m.key)}</text>{/if}
      {#if m.newYear}<text x={cx} y={cy-r+8} text-anchor="middle" font-size={compact?22:12} font-weight="600" fill="#373450" stroke="#fff" stroke-width="5" paint-order="stroke" pointer-events="none">{fill('spiralYearRing',{year:m.key.slice(0,4)})}</text>{/if}
    {/each}
  {:else if days>1}{#each dateTicks as day}{@const p=point(inner+(day+.5)*ring,16.5)}
    <circle cx={p[0]} cy={p[1]} r="1.5" fill="#575279" pointer-events="none"/>
    <text x={p[0]-7} y={p[1]-3} text-anchor="end" font-size={compact?22:12} fill="#575279" stroke="#fff" stroke-width="3" paint-order="stroke" pointer-events="none">{new Date(Date.parse(report.start_date)+(from+day)*86400000).toISOString().slice(0,10)}</text>
  {/each}{/if}
  <!-- Which way the days run is a full sentence, too long for the centre, so it sits with the legend. -->
  <text x={cx} y={cy+(compact?8:5)} text-anchor="middle" font-size={compact?24:13} fill="#575279" pointer-events="none">{shown} {t('days')}</text>
  <!-- The compact view shows this legend as HTML below the figure instead. -->
  <g visibility={compact?'hidden':undefined}>
  <text x="70" y={cy+395} font-size="16" fill="#373450">{metric}</text>
  <rect x="370" y={cy+379} width="300" height="18" fill={`url(#${uid}-gradient)`}/>
  <text x="362" y={cy+395} text-anchor="end" font-size="14" fill="#575279">&gt;0%</text>
  <text x="682" y={cy+395} font-size="14" fill="#575279">100%</text>
  {#each [{fill:'#f1f0f2',key:'cell_no_recording'},{fill:`url(#${uid}-missing)`,key:'missingAnalysis'},
    {fill:`url(#${uid}-partial)`,key:'cell_partial'}] as item,i}
    <rect x={70+i*285} y={cy+419} width="20" height="16" fill={item.fill} stroke="#aeacb8" stroke-width=".5"/>
    <text x={100+i*285} y={cy+432} font-size="14" fill="#575279">{t(item.key)}</text>{/each}
  <rect x="70" y={cy+450} width="20" height="16" fill="#fff" stroke="#aeacb8"/><text x="100" y={cy+463} font-size="14" fill="#575279">{t('uZeroCalls')}</text>
  <text x="355" y={cy+463} font-size="14" fill="#575279">{t('dateOutward')}</text>
  <text visibility={compact?'hidden':undefined} x="70" y={cy+494} font-size="14" fill="#575279">{t(quality?.status === 'decided' ? 'wFigureCaution' : 'detectionFigureCaution')}</text>
{#if quality}<text visibility={compact?'hidden':undefined} x="70" y={cy+523} font-size="14" fill="#575279">{t('wQuality')}: {checkText(quality)}</text>{/if}
  </g>
</svg>
</div>
{#if months.length}<p class="view" role="status">{month ? fill('spiralMonthHint',{month:month.name}) : t('spiralWholeHint')}</p>{/if}
<!-- On screen the figure is scaled down, so its legend is plain text at a readable size; exports keep the SVG legend. -->
{#if compact}<div class="legend">
  <span><i class="ramp"></i>{t('rampLegend')}</span>
  <span><i style:background="#fff"></i>{t('uZeroCalls')}</span>
  <span><i style:background="#f1f0f2"></i>{t('cell_no_recording')}</span>
  <span><i class="missing"></i>{t('missingAnalysis')}</span>
  <span><i class="partial"></i>{t('cell_partial')}</span>
  <span>{t('dateOutward')}</span>
</div><p class="caution">{t(quality?.status === 'decided' ? 'wFigureCaution' : 'detectionFigureCaution')}</p>{/if}

<style>
  .actions { margin-bottom: 12px; }
  .view { margin: 8px 0 0; }
  .plot-scroll { overflow: auto; max-height: 780px; border: 1px solid rgb(var(--stone-300)); background: #fff; }
  svg { display: block; width: 100%; max-width: 720px; height: auto; min-width: 600px; margin: 0 auto; }
  .plot-scroll.compact{border:0;max-height:none}.compact svg{min-width:0;max-width:none}
  .legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin-top: 8px; font-size: 13px; color: rgb(var(--stone-600)); }
  .caution { margin: 8px 0 0; font-size: 14px; color: rgb(var(--stone-600)); }
  .legend span { display: inline-flex; align-items: center; gap: 8px; }
  .legend i { display: inline-block; width: 20px; height: 16px; border: 1px solid #aeacb8; }
  .legend .ramp { width: 48px; background: linear-gradient(90deg, #e2bcba, #a0504c); }
  .legend .missing { background: repeating-linear-gradient(-45deg, #fafafa 0 3px, #aeacb8 3px 4px); }
  .legend .partial { background: radial-gradient(circle, #494454 1px, transparent 1.5px) 0 0 / 6px 6px, #f1f0f2; }
  path[role=button] { cursor: pointer; }
  path[role=button]:focus { outline: none; stroke: #286983; stroke-width: 2px; }
</style>
