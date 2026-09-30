"""Readable HTML reports with native tables and audio."""
import json
from datetime import date,datetime
from html import escape
from pathlib import Path
from zoneinfo import ZoneInfo

from report_figures import DATA


def messages(locale):
    return json.loads((DATA/'messages.json').read_text(encoding='utf-8')).get(locale) or messages('en')


def name(species,locale):
    common=species['common_names'].get(locale) or species['common_names'].get('en')
    return (common+'\n' if common and common!=species['scientific_name'] else '')+species['scientific_name']


def number(value,empty='—'):
    return empty if value is None else f'{value:,}'


def percent(value,empty='—'):
    return empty if value is None else f'{100*value:.1f}%'


def quality_result(snap):
    validation=snap.get('validation')
    engine=snap.get('engine','search')
    if not validation or not validation['applicable']:return None
    return validation.get('results',{}).get(engine)


def fixed_precision(validation):
    return bool(validation and validation.get('method')=='fixed_precision_audit_v1')


def status(snap):
    validation=snap.get('validation');result=quality_result(snap)
    if not validation:return 'not_evaluated'
    if not validation['applicable']:return 'stale'
    if fixed_precision(validation):
        if not result or not result.get('precision_complete'):return 'pending'
        return 'goal_met' if result.get('goal_met') else 'goal_not_met'
    if validation['stage']=='calibration':return 'calibrating'
    if validation['stage']!='complete':return ('interim_goal_met' if result['goal_met'] else 'interim') if result else 'evaluating'
    return 'goal_met' if result and result['goal_met'] else 'goal_not_met'


def status_text(snap,t):
    key=status(snap)
    validation=snap.get('validation')
    if fixed_precision(validation) and key in ('goal_met','goal_not_met'):
        return t['fixed_'+key].format(target=percent(validation.get('target_precision')))
    return t[key]


def criterion(snap,t):
    if snap['threshold'] is None:return t['unset']
    if snap.get('engine')=='custom':return f"{snap['threshold']:.3f}\n{t['custom_cv_threshold']}"
    validation=snap.get('validation');engine=snap.get('engine','search')
    adjusted=validation and validation['applicable'] and validation.get('thresholds',{}).get(engine,{}).get('adjusted')
    label=t['adjusted'] if adjusted else t['default_unadjusted'] if validation else t['default']
    return f"{snap['threshold']:.2f}\n{label}"


def method(snap,t):
    if snap.get('engine')=='custom':return f"{t['custom_model']} #{snap['model_id']}"
    return ('BirdNET' if snap['engine']=='birdnet' else 'Perch') if snap['kind']=='detection' else f"{t['search']} #{snap['run_id']}"


def model_group(snap,t):
    engine=snap.get('engine','search')
    return 'BirdNET' if engine=='birdnet' else 'Perch' if engine=='perch' else t['custom_model'] if engine=='custom' else t['search']


def result_counts(snapshots):
    flagged={s['species']['id'] for s in snapshots if s['kind']=='detection' and s['threshold'] is not None
             and any(r.get('candidate_windows') for r in s['summaries'])}
    checked=set();met=set()
    for snap in snapshots:
        validation=snap.get('validation');result=quality_result(snap)
        if validation and validation.get('applicable') and result and result.get('precision_complete'):
            checked.add(snap['species']['id'])
            if result.get('goal_met'):met.add(snap['species']['id'])
    return len(flagged),len(checked),len(met)


def metric(value,t):
    if not value:return t['unavailable']
    point=percent(value.get('value'),t['unavailable'])
    if value.get('low') is None:return point
    return point+f"\n[{percent(value['low'])}–{percent(value['high'])}]"


def audit_sample(sample,t):
    if not sample:return t['unavailable']
    return t['audit_sample'].format(**{k:number(sample.get(k)) for k in ('answered','total','target','unknown','population')})


def blocks(manifest,snapshots,references,quiz):
    t=messages(manifest['locale']);locale=manifest['locale'];scope=manifest['scope'];site=manifest['site'];output=[]
    def heading(title):output.append({'type':'heading','text':title})
    def para(key,**values):output.append({'type':'paragraph','text':t[key].format(**values)})
    def table(headers,rows,widths,tabs=None):output.append({'type':'table','headers':headers,'rows':rows,'widths':widths,'tabs':tabs})
    def section(title):output.append({'type':'break'});heading(t[title])
    output.append({'type':'title','text':t['title']});para('intro');heading(t['scope'])
    table([t['scope'],t['summary']],[[t['site'],site['name']],
        [t['period'],f"{manifest['start_date']} — {manifest['end_date']}"],
        [t['coordinates'],f"{site['latitude']:.6f}, {site['longitude']:.6f}"],
        [t['timezone'],site['timezone']],
        [t['recordings'],number(len(scope['recording_ids']))],
        [t['recording_hours'],f"{scope['recorded_seconds']/3600:.2f}"],
        [t['recording_days'],number(sum(d['seconds']>0 for d in scope['daily']))]], [1,2.6])
    para('scope_note')
    heading(t['coverage'])
    table([t['method'],t['analysed_hours']],[[key.replace('Search ',t['search']+' '),f'{value/3600:.2f}'] for key,value in scope['analysed_seconds'].items()],[1,1])
    if any(value+1<scope['recorded_seconds'] for value in scope['analysed_seconds'].values()):para('partial_coverage')
    para('coverage_note')
    detected,checked,met=result_counts(snapshots)
    searched={s['species']['id'] for s in snapshots if s['kind']=='search' and any(r['candidate_windows'] for r in s['summaries'])}
    para('ai_flagged_sentence',detected=detected)
    para('checked_sentence',checked=checked,met=met)
    if any(s['kind']=='search' and s['threshold'] is not None for s in snapshots):para('search_sentence',searched=len(searched))
    unset=sum(s['kind']=='search' and s['threshold'] is None for s in snapshots)
    if unset:para('unset_search_sentence',count=unset)
    para('selected_note')
    output.append({'type':'image','path':'figures/recording-schedule.png','caption':t['effort'],'width':1600,'height':max(450,120+len(scope.get('schedule',scope['daily']))*16)})
    section('reading');para('threshold_explanation');para('quality_explanation');para('unit_explanation')
    heading(t['limits']);para('limits_text')
    section('species_list');para('unit_explanation')
    rows=[];groups={}
    for snap in snapshots:
        summary=snap['summaries'][0] if snap['summaries'] else {}
        row=[name(snap['species'],locale),method(snap,t),criterion(snap,t),
            (f"{number(summary.get('candidate_windows'))} / {number(summary.get('eligible_windows'))}\n{percent(summary.get('candidate_rate'))}" if snap['threshold'] is not None else t['unset_count'].format(eligible=number(summary.get('eligible_windows')))),
            number(summary.get('candidate_days')),status_text(snap,t),snap['figure_id']]
        rows.append(row);groups.setdefault(model_group(snap,t),[]).append(row)
    table([t[k] for k in ('species_name','method','criterion','count','days','quality','figure')],rows,[2.6,1.1,1.3,1.6,.6,1.6,.6],groups)
    if manifest.get('manual_species'):
        heading(t['annotation_title']);para('annotation_note')
        table([t['species_name'],t['annotation_count']],[[name(r['species'],locale),number(r['intervals'])] for r in manifest['manual_species']],[2,1])
    days=(date.fromisoformat(manifest['end_date'])-date.fromisoformat(manifest['start_date'])).days+1
    practice={q['species_id']:q for q in quiz['per_species']}
    section('details');para('legend_detection')
    if any(s['kind']=='search' for s in snapshots):para('legend_search')
    para('legend_missing');cards=[]
    for snap in snapshots:
        summary=snap['summaries'][0] if snap['summaries'] else {};q=practice.get(snap['species']['id'])
        text=[f"{snap['figure_id']} · {name(snap['species'],locale)}",method(snap,t),
            (t['count_sentence'].format(positive=number(summary.get('candidate_windows')),eligible=number(summary.get('eligible_windows')),
                rate=percent(summary.get('candidate_rate')),days=number(summary.get('candidate_days')),window=snap['window_s']) if snap['threshold'] is not None else t['unset_count_sentence'].format(eligible=number(summary.get('eligible_windows')))),
            t['criterion']+': '+criterion(snap,t).replace('\n',' · '),status_text(snap,t)]
        if snap['kind']=='search':
            text.append(t['search_confirmed'].format(count=snap['judgements']['target']))
        if q:text.append(t['quiz_short'].format(**q))
        cards.append({'text':text,'path':f"figures/{snap['figure_id']}.png",'id':snap['figure_id'],'species_id':snap['species']['id'],'kind':snap['kind'],'run_id':snap.get('run_id'),'reference_ids':snap.get('reference_ids',[])})
    output.append({'type':'cards','cards':cards,'columns':2 if days<=93 else 1})
    section('quality_title');para('precision_note');para('validation_scope');para('human_reference')
    if any(fixed_precision(s.get('validation')) for s in snapshots):para('fixed_precision_scope')
    shown_validations=set()
    for snap in snapshots:
        validation=snap.get('validation')
        if validation and (validation.get('annotation_session_id') or validation.get('excluded_recording_ids')) and validation['id'] not in shown_validations:
            para('custom_holdout' if validation.get('model_id') else 'annotation_holdout',id=validation['id'],count=len(validation.get('excluded_recording_ids',[])),unit=t['split_'+validation['split_unit']])
            shown_validations.add(validation['id'])
    if any('checkpoints' in s.get('validation',{}).get('uncertainty','') for s in snapshots if s.get('validation')):para('checkpoint_note')
    rows=[];groups={}
    for snap in snapshots:
        validation=snap.get('validation')
        if not validation:continue
        result=quality_result(snap);counts=validation['counts']
        if fixed_precision(validation):
            precision=metric(result.get('precision') if result and result.get('precision_complete') else None,t)
            if result and result.get('precision_complete') and result.get('precision_lower_95') is not None:
                precision+='\n'+t['one_sided_lower'].format(value=percent(result['precision_lower_95']),target=percent(validation.get('target_precision')))
            recall=metric(result.get('recall') if result and result.get('recall_complete') else None,t)
            if not result or not result.get('recall_complete'):recall+='\n'+t['recall_not_established']
            samples=audit_sample(result.get('precision_sample') if result else None,t)+'\n'+t['recall_sample_label']+': '+audit_sample(result.get('recall_sample') if result else None,t)
        else:
            precision=metric(result.get('precision') if result else None,t);recall=metric(result.get('recall') if result else None,t)
            samples=t['legacy_samples'].format(calibration=counts['calibration']['answered'],
                evaluation=validation.get('evaluated_count',counts['evaluation']['answered']),
                unresolved=number(result.get('unknown') if result else None))+f"\nV{validation['id']}"
        row=[name(snap['species'],locale)+'\n'+method(snap,t),criterion(snap,t),precision,recall,samples,status_text(snap,t)]
        rows.append(row);groups.setdefault(model_group(snap,t),[]).append(row)
    if rows:table([t[k] for k in ('species_name','criterion','precision','recall','samples','quality')],rows,[2.2,1.2,1.5,1.5,1.6,1.7],groups)
    else:para('validation_none')
    custom=[s for s in snapshots if s.get('engine')=='custom']
    if custom:
        heading(t['custom_training_title']);para('custom_models_note');rows=[]
        for snap in custom:
            info=snap['custom_model'];training=info.get('training',{});cv=info.get('cv',{})
            precision=cv.get('reference_precision') or {};recall=cv.get('reference_recall') or {}
            rows.append([name(snap['species'],locale)+'\n'+method(snap,t),
                t['custom_training_count'].format(positive=number(training.get('positive_5s')),negative=number(training.get('negative_5s')),groups=number(training.get('groups'))),
                f"{percent(precision.get('value'))}\n{number(precision.get('positive'))} / {number(precision.get('predicted'))}",
                f"{percent(recall.get('value'))}\n{number(recall.get('found'))} / {number(recall.get('present'))}"])
        table([t[k] for k in ('species_name','custom_training_material','custom_training_precision','custom_training_recall')],rows,[2,2.5,1.5,1.5])
    section('quiz_title');para('quiz_note')
    taxa={s['id']:s for s in manifest['selected_species']}
    rows=[]
    for q in quiz['per_species']:
        moment=datetime.fromisoformat(q['answered_at']).astimezone(ZoneInfo(site['timezone'])).strftime('%Y-%m-%d %H:%M')
        rows.append([name(taxa[q['species_id']],locale),f"{q['correct']} / {q['total']}",str(q['unknown']),moment,
            f"Q{q['session_id']} · {t['practice']}"+('' if q['complete'] else f" ({t['partial_quiz']})")+f"\n{q['set_sha256'][:12]}"])
    if rows:table([t[k] for k in ('species_name','quiz_score','quiz_unknown','quiz_date','quiz_set')],rows,[2.4,1,1,1.6,2])
    else:para('quiz_none')
    para('quiz_link_note');output.append({'type':'paragraph','text':site['timezone']})
    if references:
        section('references');para('reference_note')
        for ref in references:
            output.append({'type':'heading','id':ref['id'],'text':ref['id']+' · '+name(taxa[ref['species_id']],locale)})
            if ref.get('spectrogram'):
                output.append({'type':'image','path':ref['spectrogram'],'caption':t['reference_interval']+f" {ref['query']['start_s']:g}–{ref['query']['end_s']:g} s",'width':1040,'height':380})
            else:para('reference_missing')
            credit=ref.get('attribution') or {}
            values=[credit.get(k) for k in ('recordist','url','license') if credit.get(k)]
            if values:output.append({'type':'paragraph','text':t['reference_source']+': '+' · '.join(str(v) for v in values)})
            if not all(credit.get(k) for k in ('recordist','url','license')):para('unknown_credit')
            output.append({'type':'reference','reference':ref})
    section('files');para('files_note');para('evidence_note')
    table([t['file'],t['contents']],manifest['file_guide'],[1.3,3])
    heading(t['methods_title']);para('methods_note');para('search_unset');para('human_reference')
    output.append({'type':'paragraph','text':f"{t['saved']}: {saved_time(manifest)}\nID: {manifest['package_id']}\nSchema: {manifest['schema']}"})
    return output


def saved_time(manifest):
    return datetime.fromisoformat(manifest['saved_at']).astimezone(ZoneInfo(manifest['site']['timezone'])).strftime('%Y-%m-%d %H:%M')+' · '+manifest['site']['timezone']


def create_html(folder,manifest,snapshots,references,evidence,quiz):
    """A portable file:// report: no network, fetch, framework or embedded audio blobs."""
    content=blocks(manifest,snapshots,references,quiz)
    t=messages(manifest['locale']);e=escape;parts=[];plain=[];section=None;section_number=0
    title_ids={t['reading']:'reading',t['species_list']:'species',t['details']:'figures',
        t['quality_title']:'quality',t['quiz_title']:'quiz',t['references']:'references',t['files']:'files'}
    seen_titles=set();figure_ids={s['figure_id'] for s in snapshots}
    def player(path,label):
        return f'<button class="play" type="button" data-nocopy data-audio="{e(path,quote=True)}" data-play="{e(t["audio"],quote=True)}" data-pause="{e(t["pause"],quote=True)}" aria-label="{e(label,quote=True)}" aria-pressed="false">{e(t["audio"])}</button>'
    def paragraph(text):return '<p>'+e(str(text)).replace('\n','<br>')+'</p>'
    def close_section():
        nonlocal section
        if section:parts.append('</details>' if section=='details' else '</section>')
        section=None
    for block in content:
        kind=block['type']
        if kind=='break':continue
        if kind=='title':parts.append('<h1>'+e(block['text'])+'</h1>');plain.append(block['text'])
        elif kind=='heading':
            text=block['text'];key=title_ids.get(text)
            if key:
                if text in seen_titles:continue
                close_section();seen_titles.add(text)
                section='details' if key=='reading' else 'section'
                parts.append(f'<{section} id="{key}" class="report-section">')
                parts.append(('<summary>' if section=='details' else '<h2>')+e(text)+('</summary>' if section=='details' else '</h2>'))
            else:parts.append('<h2'+(f' id="{e(block["id"],quote=True)}"' if block.get('id') else '')+'>'+e(text)+'</h2>')
            plain.append(text)
        elif kind=='paragraph':parts.append(paragraph(block['text']));plain.append(block['text'])
        elif kind=='table':
            section_number+=1
            def render_table(rows,table_id,hidden=False,labelledby=None):
                tab_attrs=(f' tab-panel" role="tabpanel" aria-labelledby="{labelledby}" tabindex="0"' if labelledby else
                    f'" role="region" aria-label="{e(t["title"],quote=True)}" tabindex="0"')
                parts.append(f'<div class="table-scroll{tab_attrs} id="{table_id}"{" hidden" if hidden else ""}><table style="border-collapse:collapse">')
                for index,row in enumerate([block['headers'],*rows]):
                    parts.append('<thead><tr>' if index==0 else '<tr>')
                    for value in row:
                        tag='th' if index==0 else 'td';attrs=' scope="col"' if index==0 else ''
                        html=e(str(value)).replace('\n','<br>')
                        if index and str(value) in figure_ids:html=f'<a href="#{e(str(value))}">{html}</a>'
                        parts.append(f'<{tag}{attrs} style="border:1px solid #dfdad9;padding:10px;text-align:left;vertical-align:top">{html}</{tag}>')
                    parts.append('</tr></thead><tbody>' if index==0 else '</tr>')
                    plain.append('\t'.join(str(v).replace('\n',' ') for v in row))
                parts.append('</tbody></table></div>')
            tabs=block.get('tabs')
            if tabs and len(tabs)>1:
                parts.append(f'<div class="tabs" role="tablist" aria-label="{e(t["method"],quote=True)}">')
                for index,label in enumerate(tabs):
                    tab_id=f'tab-{section_number}-{index}';panel_id=f'table-{section_number}-{index}'
                    parts.append(f'<button type="button" role="tab" id="{tab_id}" aria-controls="{panel_id}" aria-selected="{str(index==0).lower()}" tabindex="{0 if index==0 else -1}">{e(label)}</button>')
                parts.append('</div>')
                for index,rows in enumerate(tabs.values()):render_table(rows,f'table-{section_number}-{index}',index>0,f'tab-{section_number}-{index}')
            else:render_table(block['rows'],f'table-{section_number}')
        elif kind=='image':
            parts.append(f'<figure><img loading="lazy" width="{block["width"]}" height="{block["height"]}" src="{e(block["path"],quote=True)}" alt="{e(block["caption"],quote=True)}"><figcaption>{e(block["caption"])}</figcaption></figure>')
            plain.append(block['caption'])
        elif kind=='cards':
            parts.append(f'<div class="cards{" wide" if block["columns"]==1 else ""}">')
            for card in block['cards']:
                parts.append(f'<article class="card" id="{e(card["id"])}"><h3>'+e(card['text'][0]).replace('\n','<br>')+'</h3>')
                parts.append(f'<a class="figure-link" href="{e(card["path"],quote=True)}" aria-label="{e(t["save_figure"],quote=True)}"><img loading="lazy" width="1920" height="2240" src="{e(card["path"],quote=True)}" alt="{e(card["text"][0],quote=True)}"></a>')
                for text in card['text'][1:]:
                    if text:parts.append(paragraph(text))
                if card['reference_ids']:parts.append('<p>'+e(t['references'])+': '+', '.join(f'<a href="#{e(r)}">{e(r)}</a>' for r in card['reference_ids'])+'</p>')
                parts.append(f'<nav data-nocopy><a href="{e(card["path"],quote=True)}" download>{e(t["save_figure"])}</a><a href="#audio-{e(card["species_id"])}">{e(t["audio"])}</a><a href="#species">{e(t["back_summary"])}</a></nav></article>')
                plain.extend(card['text'])
            parts.append('</div>')
        elif kind=='reference':
            ref=block['reference']
            if ref.get('clip_file'):
                parts.append(player(ref['clip_file'],ref['id']))
                parts.append(f'<p data-nocopy><a href="{e(ref["original_file"],quote=True)}">{e(t["open_original"])}</a> · {e(Path(ref["source"]["path"]).name)}</p>')
    close_section()
    parts.append('<section class="report-section" id="audio"><h2>'+e(t['audio'])+'</h2>'+paragraph(t['audio_note']))
    unknown=any(r['species_id'] is None for r in evidence if r.get('audio_role')=='annotation')
    for taxon in [*manifest['selected_species'],*([{'id':None,'scientific_name':t['annotation_unknown'],'common_names':{}}] if unknown else [])]:
        rows=[row for row in evidence if row['species_id']==taxon['id'] and row.get('audio_role')]
        parts.append(f'<details id="audio-{e(taxon["id"] or "unknown")}"><summary>{e(name(taxon,manifest["locale"]))}</summary>')
        if not rows:parts.append(paragraph(t['no_saved_audio']))
        for role,title in [('annotation','annotation_title'),('validation','validation_audio'),('search_positive','search_audio'),('ai_example','ai_audio')]:
            values=[row for row in rows if row['audio_role']==role]
            if not values:continue
            parts.append('<h3>'+e(t[title])+'</h3>')
            # One native player is created on demand, even for thousands of saved answers.
            for row in values:
                moment=datetime.fromisoformat(row['local_time']).strftime('%Y-%m-%d %H:%M:%S') if row.get('local_time') else ''
                description=f"{moment} · {row['start_s']:g}–{row['end_s']:g} s"
                if role=='validation':description+=' · '+t['verdict_'+row['verdict']]+f" · V{row['validation_id']}"
                elif role=='annotation':description+=f" · {t['annotation_answer']} {row['answer_id']}"
                else:description+=' · '+row['figure_id']
                parts.append('<div class="sound">'+paragraph(description))
                if role=='annotation':
                    parts.append(f'<img loading="lazy" width="1040" height="380" src="{e(row["spectrogram"],quote=True)}" alt="{e(t["annotation_spectrum"],quote=True)}">')
                if row.get('audio_file'):
                    parts.append(player(row['audio_file'],description))
                else:parts.append(paragraph(t['audio_unavailable']))
                parts.append('</div>')
        parts.append('</details>')
    parts.append('</section>')
    css=(DATA/'report.css').read_text(encoding='utf-8')
    script=(DATA/'report.js').read_text(encoding='utf-8')
    nav=''.join(f'<a href="#{key}">{e(t[label])}</a>' for key,label in [('species','species_list'),('quality','quality_title'),('quiz','quiz_title'),('audio','audio')])
    html=f'''<!doctype html><html lang="{manifest['locale']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>{e(t['title'])} · {e(manifest['site']['name'])}</title><style>{css}</style></head><body>
<header><img src="figures/echoroo.png" width="32" height="32" alt=""><span>echoroo</span><span class="stamp">{e(manifest['package_id'])}</span></header>
<div class="tools"><nav>{nav}</nav><button id="copy" type="button" data-success="{e(t['copied'],quote=True)}" data-failure="{e(t['copy_failed'],quote=True)}">{e(t['copy'])}</button><p id="copy-status" role="status"></p><p>{e(t['copy_hint'])}</p></div>
<main>{''.join(parts)}</main><textarea hidden id="plain-text">{e(chr(10).join(plain))}</textarea><footer>{e(saved_time(manifest))}</footer><script>{script}</script></body></html>'''
    (folder/'index.html').write_text(html,encoding='utf-8')
    (folder/'report.txt').write_text('\n\n'.join(plain),encoding='utf-8')
    (folder/'report-content.json').write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
