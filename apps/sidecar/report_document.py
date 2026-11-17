"""Readable HTML reports: a summary, one place per species, the recordings, then folded evidence and methods."""
import json
from datetime import date,datetime,time,timedelta,timezone
from html import escape
from math import ceil
from zoneinfo import ZoneInfo

from report_figures import DATA,common_name,custom_method


def messages(locale):
    return json.loads((DATA/'messages.json').read_text(encoding='utf-8')).get(locale) or messages('en')


def names(species,locale):
    return common_name(species,locale),species['scientific_name']


def name(species,locale):
    common,scientific=names(species,locale)
    return common or scientific


def number(value,empty='—'):
    return empty if value is None else f'{value:,}'


def percent(value,empty='—'):
    return empty if value is None else f'{100*value:.1f}%'


def share(value):
    return '—' if value is None else '<1%' if 0<value<.01 else f'{value:.0%}'


def day_text(t,value):
    return t['day'].format(month=value.month,day=value.day,mon=t['months'][value.month-1])


def date_text(t,value):
    value=date.fromisoformat(value) if isinstance(value,str) else value
    return t['date'].format(year=value.year,month=value.month,day=value.day,mon=t['months'][value.month-1])


def period_text(t,manifest):
    start,end=date_text(t,manifest['start_date']),date_text(t,manifest['end_date'])
    return start if start==end else t['period_range'].format(start=start,end=end)


def clock(value,seconds=False):
    return f'{value.hour}:{value.minute:02d}'+(f':{value.second:02d}' if seconds else '')


def duration_text(seconds,t):
    """40 min, 12 h or 1 h 30 min, rounded to the minute."""
    if seconds<60:return t['seconds'].format(s=round(seconds))
    h,m=divmod(round(seconds/60),60)
    return t['minutes'].format(m=m) if not h else t['hours'].format(h=f'{h:,}') if not m else t['hours_minutes'].format(h=f'{h:,}',m=m)


def count_of(t,key,count):
    """'1 clip' / '12 clips': the singular form only where a language has one."""
    return t[key+'_one' if count==1 and key+'_one' in t else key].format(count=number(count))


def moment_text(t,value):
    return f"{date_text(t,value.date())} {clock(value)}"


def joined(items,t):
    items=list(items)
    return items[0] if len(items)==1 else t['list_sep'].join(items[:-1])+t['list_and']+items[-1]


def quality_result(snap):
    validation=snap.get('validation')
    if not validation or not validation['applicable']:return None
    return validation.get('results',{}).get(snap['engine'])


def status(snap):
    """not_evaluated, stale, in_progress, decided or undecided."""
    validation=snap.get('validation');result=quality_result(snap)
    # A check nobody has answered yet reads as not started, not as halfway.
    if not validation or (validation.get('counts') or {}).get('answered')==0:return 'not_evaluated'
    if not validation['applicable']:return 'stale'
    if validation['stage']!='complete':return 'in_progress'
    return result.get('status','undecided') if result else 'undecided'


def status_text(snap,t):
    """The short state drawn into each spiral figure's caption."""
    value=status(snap)
    if value=='undecided':
        reason=(quality_result(snap) or {}).get('reason')
        return t['undecided_no_target' if reason=='no_target' else 'undecided']
    if value!='decided':return t[value]
    score=f"{snap['threshold']:.2f}";p=quality_result(snap).get('precision') or {}
    if None in (p.get('value'),p.get('low'),p.get('high')):return t['decided_score'].format(score=score)
    return t['decided_precision'].format(score=score,value=f"{p['value']:.0%}",low=f"{p['low']:.0%}",high=f"{p['high']:.0%}",
                                         answered=snap['validation']['counts']['answered'])


def ai_state(snap):
    """The report's three states: checked, reference (default threshold) or unused (no threshold)."""
    if snap['threshold'] is None:return 'unused'
    return 'checked' if status(snap)=='decided' else 'reference'


def method(snap,t):
    if snap['engine']=='custom':
        return custom_method(snap,t)
    from models import REGISTRY
    return REGISTRY[snap['engine']]['name']


def model_group(snap,t):
    return t['custom_model'] if snap['engine']=='custom' else method(snap,t)


def about(value,t):
    """A 90% interval in plain words; wider than 30 points also says it is wide."""
    if not value or value.get('value') is None:return t['unavailable']
    low,high=value.get('low'),value.get('high')
    if low is None or high is None:return t['about_only'].format(value=f"{value['value']:.0%}")
    text=t['about'].format(value=f"{value['value']:.0%}",low=f'{low:.0%}',high=f'{high:.0%}')
    return text+t['range_wide'] if high-low>.3 else text


def windows_text(snapshots,t):
    """The interval lengths of the AIs in this report, said once when they are all the same."""
    windows={model_group(snap,t):snap['window_s'] for snap in snapshots};lengths=set(windows.values())
    if len(windows)>1 and len(lengths)==1:return t['reading_window_all'].format(s=f'{lengths.pop():g}')
    return t['list_sep'].join(t['reading_window'].format(model=k,s=f'{v:g}') for k,v in windows.items())


def ranged(value):
    return bool(value) and None not in (value.get('value'),value.get('low'),value.get('high'))


def found_none(snap):
    """A counted AI that found no interval of this species: no precision to speak of."""
    return snap['threshold'] is not None and bool(snap['summaries']) and snap['summaries'][0].get('candidate_windows')==0


def not_found_text(row,t):
    """Names the list the person listened to: AI scores or similarity to the search example."""
    n=row.get('listened_without_confirming')
    return t['badge_not_found'].format(order=t['order_search' if row.get('not_found_list')=='search' else 'order_ai'],
                                       count=number(n),unit=t['unit_sound' if n==1 else 'unit_sounds'])


def rule_text(snap,t):
    return t['rule_checked' if ai_state(snap)=='checked' else 'rule_default'].format(score=f"{snap['threshold']:.2f}")


def counted_of(snap,t):
    """'800 sounds cut every 3 s, 77 of them': the window length explains why AIs examined different numbers."""
    s=snap['summaries'][0] if snap['summaries'] else {}
    return t['counted_of'].format(count=number(s.get('candidate_windows')),eligible=number(s.get('eligible_windows')),
                                  rate=percent(s.get('candidate_rate')),s=f"{snap['window_s']:g}")


def count_text(snap,t):
    s=snap['summaries'][0] if snap['summaries'] else {}
    return t['count_text'].format(counted=counted_of(snap,t),days=number(s.get('candidate_days')))


def estimate(value):
    """Rounded to 2 significant digits like the app (Math.round, then Intl maximumSignificantDigits:2)."""
    n=int(value+.5)
    if n<100:return n
    q=10**(len(str(n))-2)
    return (n+q//2)//q*q


def precision_text(snap,t):
    state=ai_state(snap)
    if state=='checked':
        p=quality_result(snap).get('precision') or {}
        if None in (p.get('value'),p.get('low'),p.get('high')):return t['precision_missing']
        return t['precision_text'].format(value=f"{p['value']:.0%}",low=f"{p['low']:.0%}",high=f"{p['high']:.0%}",
                                          answered=snap['validation']['counts']['answered'])
    return t['precision_'+{'in_progress':'in_progress','stale':'stale'}.get(status(snap),'unknown')]


def unused_text(snap,t,label):
    reason=(quality_result(snap) or {}).get('reason')
    return t['unused_no_target' if reason=='no_target' else 'unused_other'].format(name=label)


def local(row):
    return datetime.fromisoformat(row['local_time']) if row.get('local_time') else None


def sounds(rows,t,note):
    items=[]
    for row in sorted(rows,key=lambda r:r.get('local_time') or ''):
        moment=local(row);text=f"{day_text(t,moment)} {clock(moment,True)}" if moment else f"{row['start_s']:g}–{row['end_s']:g} s"
        items.append({'text':text,'note':note(row),'audio':row.get('audio_file') or None,'label':t['play_label'].format(time=text)})
    return {'type':'sounds','items':items}


def answer_counts(validation):
    bins=validation['counts'].get('bins') or []
    return {key:sum(b.get(key,0) for b in bins) for key in ('heard','not_heard','unknown')}


def answers_text(counts,t):
    """'聞こえる 14、聞こえない 46', or 'すべて「判断できない」' when every answer is the same."""
    parts=[(t['verdict_'+v],counts[k]) for k,v in (('heard','target'),('not_heard','other'),('unknown','unknown')) if counts.get(k)]
    if len(parts)==1:return t['answers_all'].format(verdict=parts[0][0])
    return t['list_sep'].join(t['answer_item'].format(label=label,count=number(n)) for label,n in parts)


def unchecked_text(snaps,t):
    """A species without confirmed calls whose checks still got answers (e.g. all "cannot tell")."""
    seen={snap['validation']['id']:answer_counts(snap['validation']) for snap in snaps if (snap.get('validation') or {}).get('counts')}
    counts={k:sum(c[k] for c in seen.values()) for k in ('heard','not_heard','unknown')}
    total=sum(counts.values())
    return t['badge_listened'].format(count=number(total),answers=answers_text(counts,t)) if total else t['badge_unchecked']


def blocks(manifest,snapshots,quiz,evidence=()):
    """Report content shared by index.html, report.txt and report-content.json."""
    t=messages(manifest['locale']);locale=manifest['locale'];scope=manifest['scope'];site=manifest['site']
    presence={row['species_id']:row for row in manifest['presence']}
    taxa=sorted(manifest['selected_species'],key=lambda s:presence[s['id']]['status']!='confirmed')
    per_species={s['id']:[snap for snap in snapshots if snap['species']['id']==s['id']] for s in taxa}
    days=(date.fromisoformat(manifest['end_date'])-date.fromisoformat(manifest['start_date'])).days+1
    recording_days=sum(d['seconds']>0 for d in scope['daily'])
    para=lambda text,**extra:{'type':'paragraph','text':text,**extra}

    # Summary: the conclusion on the first screen.
    rows=[];several=len({method(snap,t) for snap in snapshots})>1
    from models import REGISTRY
    builtin=list(dict.fromkeys(s['engine'] for s in snapshots if s['engine']!='custom'))
    # An AI that has no row for a species did not examine it (e.g. BirdNET outside its site list).
    gaps=any(set(builtin)-{snap['engine'] for snap in per_species[s['id']]} for s in taxa if per_species[s['id']])
    for taxon in taxa:
        common,scientific=names(taxon,locale);row=presence[taxon['id']];snaps=per_species[taxon['id']]
        heard={'confirmed':[['badge-yes',t['badge_heard']],['text',' '+count_of(t,'sounds_count',row['evidence_count'])]],
               'not_found':[['badge-no',not_found_text(row,t)]],'unchecked':[['badge-no',unchecked_text(snaps,t)]]}[row['status']]
        ai=[]
        for engine in [e for e in builtin if snaps and e not in {snap['engine'] for snap in snaps}]:
            if ai:ai.append(['br'])
            ai+=[['text',REGISTRY[engine]['name']+t['colon']],['small',t['not_examined']]]
        for snap in snaps:
            state=ai_state(snap);prefix=method(snap,t)+t['colon'] if several else ''
            if ai:ai.append(['br'])
            if state=='unused':
                reason=(quality_result(snap) or {}).get('reason')
                ai+=[['text',prefix],['badge-'+state,t['badge_'+state]],['small',t['table_unused_no_target' if reason=='no_target' else 'table_unused_other']]]
                continue
            s=snap['summaries'][0] if snap['summaries'] else {}
            if found_none(snap):
                ai+=[['text',prefix+count_of(t,'windows_count',0)+' '],['small',t['table_found_none']]]
                continue
            p=(quality_result(snap) or {}).get('precision') or {}
            ai+=[['text',prefix+count_of(t,'windows_count',s.get('candidate_windows'))+' '],['badge-'+state,t['badge_'+state]],
                 ['small',t['table_precision'].format(value=f"{p['value']:.0%}") if state=='checked' and p.get('value') is not None else '']]
        rows.append([[['a','#sp-'+taxon['id'],common or scientific],['small',scientific if common else '']],heard,ai or [['badge-no',t['badge_no_ai']]]])
    heard=[name(s,locale) for s in taxa if presence[s['id']]['status']=='confirmed']
    checked={s['id']:next(snap for snap in per_species[s['id']] if ai_state(snap)=='checked') for s in taxa
             if any(ai_state(snap)=='checked' for snap in per_species[s['id']])}
    open_checks=[name(s,locale) for s in taxa if s['id'] not in checked and any(status(snap)=='in_progress' for snap in per_species[s['id']])]
    def checked_item(taxon):
        p=(quality_result(checked[taxon['id']]) or {}).get('precision') or {}
        return name(taxon,locale)+(t['sure_precision'].format(value=f"{p['value']:.0%}") if p.get('value') is not None else '')
    # The plain answer to "how sure is this report?", the sentence a reader copies.
    sure=[t['sure_heard'].format(names=joined(heard,t)) if heard else t['sure_heard_none'],
          t['sure_checked'].format(names=joined([checked_item(s) for s in taxa if s['id'] in checked],t)) if checked else t['sure_checked_none']]
    if open_checks:sure.append(t['sure_in_progress'].format(names=joined(open_checks,t)))
    others=[s for s in taxa if presence[s['id']]['status']!='confirmed' and s['id'] not in checked]
    if others:sure.append(t['sure_all_candidates' if len(others)==len(taxa) else 'sure_others'])
    summary=[para(t['scope_line'].format(site=site['name'],period=period_text(t,manifest),hours=duration_text(scope['recorded_seconds'],t),
                                          files=number(len(scope['recording_ids']))),cls='scope'),
        {'type':'tiles','items':[[t['tile_value'].format(count=len(heard)),t['tile_heard']],
            [t['tile_value'].format(count=len(checked)),t['tile_counted']],
            [t['tile_value'].format(count=len(taxa)),t['tile_selected']]]},
        para(t['sentence_sep'].join(sure),cls='sure'),
        {'type':'table','headers':[t['col_species'],t['col_heard'],t['col_ai']],'rows':rows}]
    other=manifest.get('other_species') or {}
    left_out=[t['other_'+key].format(count=number(other[key])) for key in ('candidates','heard') if other.get(key)]
    if left_out:summary.append(para(t['sentence_sep'].join(left_out+[t['other_include']])))
    # Answers "which birds were here?" without implying anyone confirmed them.
    if other.get('candidate_species'):
        summary.append({'type':'details','summary':t['other_list_title'].format(count=number(len(other['candidate_species']))),'body':[
            para(t['other_list_note'],muted=True),{'type':'table','headers':[t['col_species'],t['other_list_col']],
            'rows':[[[['text',name(row['species'],locale)],['small',' '+row['species']['scientific_name'] if common_name(row['species'],locale) else '']],
                     t['list_sep'].join(t['other_list_count'].format(model=model,clips=count_of(t,'windows_count',count)) for model,count in row['windows'])]
                    for row in other['candidate_species']]}]})
    summary.append(para(t['absence_note'],muted=True))
    output=[{'type':'section','id':'summary','title':t['title'],'level':1,'body':summary}]

    # Species: how to read AI detections once, then one section per species.
    # Name only the interval lengths of the AIs in this report.
    reading=t['reading_windows'].format(windows=windows_text(snapshots,t))
    # Each built-in AI is introduced once, with who made it.
    reading+=''.join(t['about_'+e.split('-')[0]].format(name=REGISTRY[e]['name']) for e in dict.fromkeys(s['engine'] for s in snapshots) if e!='custom')
    if len({model_group(snap,t) for snap in snapshots})>1:reading+=t['reading_no_sum']
    output.append({'type':'section','id':'species','title':t['nav_species'],'body':[
        {'type':'heading','text':t['reading_title']},para(reading),para(t['reading_figure']),
        {'type':'legend','items':[['#a24f4f',t['legend_red']],['#ffffff',t['legend_white']],['#f1f0f2',t['legend_gray']]]},
        para(t['legend_note'],muted=True)] if snapshots else []})
    origin=lambda row:t['origin_'+row.get('origin','check')]
    for taxon in taxa:
        common,scientific=names(taxon,locale);label=common or scientific;row=presence[taxon['id']];snaps=per_species[taxon['id']]
        body=[]
        if row['status']=='confirmed':
            heard=[r for r in evidence if r.get('audio_role')=='presence' and r['species_id']==taxon['id']]
            listed=sounds(heard,t,origin)
            body+=[{'type':'heading','text':t['heard_title'],'badge':['yes',t['badge_heard']],'after':' '+count_of(t,'sounds_count',row['evidence_count'])},
                   para(t['heard_note'].format(name=label),muted=True)]
            if listed['items']:body.append({**listed,'items':listed['items'][:5]})
            if len(listed['items'])>5:
                body.append({'type':'details','summary':t['heard_more'].format(count=len(listed['items'])-5),'body':[{**listed,'items':listed['items'][5:]}]})
        else:
            body.append({'type':'heading','text':t['heard_title'],'badge':['no',not_found_text(row,t) if row['status']=='not_found' else unchecked_text(snaps,t)]})
            if row['status']=='not_found':body.append(para(t['not_found_text']))
        if row.get('region')=='unusual':body.append(para(t['region_unusual_note'],muted=True))
        if not snaps:
            body+=[{'type':'heading','text':t['ai_title'],'badge':['no',t['badge_no_ai']]},para(t['ai_none'])]
        for snap in snaps:
            state=ai_state(snap);model=method(snap,t)
            body.append({'type':'heading','text':t['ai_title'],'badge':['no',t['badge_found_none']] if found_none(snap) else [state,t['badge_'+state]]})
            if state=='unused':
                body+=[para(unused_text(snap,t,label)),{'type':'facts','items':[[t['fact_ai'],model]]}]
            elif found_none(snap):
                body.append({'type':'facts','items':[[t['fact_ai'],model],[t['fact_rule'],rule_text(snap,t)],
                    [t['fact_count'],t['count_none'].format(eligible=number(snap['summaries'][0].get('eligible_windows')),name=label,s=f"{snap['window_s']:g}")]]})
            else:
                body.append({'type':'facts','items':[[t['fact_ai'],model],[t['fact_rule'],rule_text(snap,t)],
                                                     [t['fact_count'],count_text(snap,t)],[t['fact_precision'],precision_text(snap,t)]]})
            if state=='reference':
                examples=[r for r in evidence if r.get('audio_role')=='ai_example' and r.get('figure_id')==snap['figure_id']]
                if examples:
                    body.append({'type':'details','summary':t['examples_title'].format(count=len(examples)),
                                 'body':[para(t['examples_note'],muted=True),sounds(examples,t,lambda r:t['score_note'].format(score=f"{float(r['score']):.2f}") if r.get('score') not in (None,'') else '')]})
        links=[['#q-'+snap['figure_id'],t['link_detail']] for snap in snaps if status(snap)!='not_evaluated'][:1]
        body.append({'type':'links','items':links+[['#summary',t['link_summary']]]})
        figures=[{'path':f"figures/{snap['figure_id']}.png",'id':snap['figure_id'],'alt':t['figure_alt'].format(name=label,model=method(snap,t)),
                  'caption':method(snap,t) if several else ''} for snap in snaps if snap['threshold'] is not None]
        output.append({'type':'section','id':'sp-'+taxon['id'],'title':common or scientific,'subtitle':scientific if common else '',
                       'figures':figures,'nofigure':t['no_figure'],'body':body})

    # Recordings: what was examined, and when recording happened (not detections).
    facts=[[t['rec_site'],site['name']],[t['rec_coordinates'],f"{site['latitude']:.6f}, {site['longitude']:.6f}"],[t['rec_timezone'],t['zone_text'].format(name=t['zone_names'].get(site['timezone'],t['zone_local']),zone=site['timezone'])],
        [t['rec_period'],t['rec_period_text'].format(period=period_text(t,manifest),days=number(recording_days))],
        [t['rec_recordings'],t['rec_recordings_text'].format(files=number(len(scope['recording_ids'])),hours=duration_text(scope['recorded_seconds'],t))],
        [t['rec_analysed'],'\n'.join(t['rec_analysed_text'].format(model=key,hours=duration_text(value,t)) for key,value in scope['analysed_seconds'].items()) or '—']]
    recordings=[{'type':'facts','items':facts}]
    if any(value+1<scope['recorded_seconds'] for value in scope['analysed_seconds'].values()):recordings.append(para(t['partial_coverage']))
    recordings+=[{'type':'heading','text':t['schedule_title']},
        {'type':'schedule','label':t['schedule_label'].format(period=period_text(t,manifest),days=number(recording_days),hours=duration_text(scope['recorded_seconds'],t)),
         'text':t['schedule_text'].format(share=share(scope['recorded_seconds']/(days*86400)),hours=duration_text(scope['recorded_seconds']/days,t))},
        {'type':'heading','text':t['limits_title']},para(t['limits_text'])]
    output.append({'type':'section','id':'recordings','title':t['nav_recordings'],'body':recordings})

    # Details: evidence and methods, each folded.
    checks=[];shown=set()
    for snap in snapshots:
        validation=snap.get('validation')
        if status(snap)=='not_evaluated':continue
        result=quality_result(snap) or {};label=name(snap['species'],locale);state=status(snap)
        source=t['source_none'] if snap['threshold'] is None else t['source_checked' if ai_state(snap)=='checked' else 'source_default']
        items=[[t['detail_threshold'],(f"{snap['threshold']:.2f}" if snap['threshold'] is not None else '—')+t['paren'].format(text=source)]]
        if state in ('in_progress','stale'):items.append([t['detail_state'],t['precision_'+state]])
        elif state=='undecided':items.append([t['detail_state'],unused_text(snap,t,label)])
        if result.get('precision'):
            items.append([t['detail_precision'],about(result['precision'],t)])
            if ranged(result['precision']):shown.add('range')
        if result:
            rare=result.get('recall_status')=='not_estimable_rare'
            items.append([t['detail_recall'],t['rare_recall'] if rare else about(result.get('recall'),t)])
            if not rare and ranged(result.get('recall')):shown.add('range')
            c=result.get('count') or {}
            if ranged(c):shown.add('range')
            if c.get('value') is not None:
                windows=(snap['summaries'][0] if snap['summaries'] else {}).get('candidate_windows')
                items.append([t['detail_estimated'],t['estimated_text'].format(value=number(estimate(c['value'])),low=number(estimate(c['low'])) if c.get('low') is not None else '—',
                                                                               high=number(estimate(c['high'])) if c.get('high') is not None else '—')
                              +(t['estimated_vs_counted'].format(counted=number(windows)) if snap['threshold'] is not None and windows is not None else '')])
        counts=answer_counts(validation)
        items.append([t['detail_answered'],t['answered_text'].format(answered=validation['counts']['answered'],**counts)])
        body=[{'type':'heading','id':'q-'+snap['figure_id'],'text':f"{label}{t['dot']}{method(snap,t)}"},{'type':'facts','items':items}]
        p=result.get('precision') or {}
        if p.get('value') is not None and snap['threshold'] is not None:
            score=f"{snap['threshold']:.2f}"
            text=t['precision_basis'].format(answered=number(validation['counts']['answered']),heard=number(counts['heard']),score=score,value=f"{p['value']:.0%}")
            # Low cut-offs read as weak detections; each AI's scores are on its own scale.
            body.append(para(text+(t['sentence_sep']+t['precision_low'].format(score=score) if snap['threshold']<.5 else ''),muted=True))
        if result.get('disagreement'):body.append(para(t['disagreement']))
        body.append(para(t['detail_files_note'],muted=True))
        answered=[r for r in evidence if r.get('audio_role')=='validation' and str(r.get('validation_id'))==str(validation['id'])]
        if answered:
            verdicts={k:sum(r.get('verdict')==v for r in answered) for k,v in (('heard','target'),('not_heard','other'),('unknown','unknown'))}
            body.append({'type':'details','summary':t['detail_play_all'].format(count=len(answered),answers=answers_text(verdicts,t)),
                         'body':[sounds(answered,t,lambda r:t['verdict_'+r['verdict']] if r.get('verdict') else '')]})
        checks+=body
    details=[para(t['details_note'],muted=True),
        {'type':'details','id':'checks','summary':t['detail_checks'],
         'body':([para(t['range_note'],muted=True)] if 'range' in shown else [])+checks
         if checks else [para(t['checks_none'])]}]
    groups={}
    for snap in snapshots:
        s=snap['summaries'][0] if snap['summaries'] else {}
        source=t['source_none'] if snap['threshold'] is None else t['source_checked' if ai_state(snap)=='checked' else 'source_default']
        groups.setdefault(model_group(snap,t),[]).append([name(snap['species'],locale),method(snap,t),
            (f"{snap['threshold']:.2f}" if snap['threshold'] is not None else '—')+t['paren'].format(text=source),
            counted_of(snap,t) if snap['threshold'] is not None else t['uncounted'],number(s.get('candidate_days')) if snap['threshold'] is not None else '—',
            t['badge_found_none'] if found_none(snap) else t['badge_'+ai_state(snap)]])
    if groups:
        details.append({'type':'details','summary':t['detail_table'],'body':[{'type':'table',
            'headers':[t[k] for k in ('col_species','col_model','col_rule','col_counted','col_days','col_state')],
            'rows':[r for rows in groups.values() for r in rows],'tabs':groups if len(groups)>1 else None}]+([para(t['not_examined_note'],muted=True)] if gaps else [])})
    decided=[f"{name(s['species'],locale)}{t['paren'].format(text=method(s,t))}" for s in snapshots if ai_state(s)=='checked']
    preset=any(ai_state(s)=='reference' for s in snapshots)
    cutoff=([t['method_cutoff_checked'].format(names=joined(decided,t))]+([t['method_cutoff_default_rest']] if preset else []) if decided
            else [t['method_cutoff_default_all']] if preset else [])
    methods=[para(t['sentence_sep'].join([t['method_check'],*cutoff,t['method_limits']])),para(t['method_scope'])]
    custom=[s for s in snapshots if s['engine']=='custom']
    if custom:
        methods+=[para(t['custom_note_short']),{'type':'table','headers':[t['col_species'],t['custom_training_material']],
            'rows':[[name(snap['species'],locale)+'\n'+method(snap,t),t['custom_training_count'].format(
                positive=number(snap['custom_model']['training'].get('positive_5s')),negative=number(snap['custom_model']['training'].get('negative_5s')))]
                for snap in custom]}]
    details.append({'type':'details','summary':t['detail_method'],'body':methods})
    names_by_id={s['id']:s for s in manifest['selected_species']}
    quiz_rows=[]
    for q in quiz['per_species']:
        if q['species_id'] not in names_by_id:continue
        moment=moment_text(t,datetime.fromisoformat(q['answered_at']).astimezone(ZoneInfo(site['timezone'])))
        quiz_rows.append([name(names_by_id[q['species_id']],locale),f"{q['correct']} / {q['total']}",str(q['unknown']),moment,
            t['quiz_session' if q['complete'] else 'quiz_session_partial'].format(n=q['session_id'])])
    if quiz_rows:
        details.append({'type':'details','summary':t['detail_quiz'],'body':[para(t['quiz_note']),
            {'type':'table','headers':[t[k] for k in ('col_species','quiz_score','quiz_unknown','quiz_date','quiz_set')],'rows':quiz_rows}]})
    details.append({'type':'details','summary':t['detail_files'],'body':[para(t['files_note']+t['sentence_sep']+t['files_readme'])]})
    output.append({'type':'section','id':'details','title':t['nav_details'],'body':details})
    return output


def saved_time(manifest):
    t=messages(manifest['locale'])
    return moment_text(t,datetime.fromisoformat(manifest['saved_at']).astimezone(ZoneInfo(manifest['site']['timezone'])))


def schedule_svg(records,manifest,t,label):
    """One line per recording at its true local start and length, split at local midnight; one hover band per day."""
    zone=ZoneInfo(manifest['site']['timezone']);first=date.fromisoformat(manifest['start_date'])
    days=(date.fromisoformat(manifest['end_date'])-first).days+1;rows=[[] for _ in range(days)]
    for record in records:
        start=datetime.fromisoformat(record['recorded_at']).astimezone(timezone.utc);end=start+timedelta(seconds=record['seconds'])
        begin=start.astimezone(zone)
        text=t['schedule_line'].format(day=day_text(t,begin),start=clock(begin),end=clock(end.astimezone(zone)),duration=duration_text(record['seconds'],t))
        piece=start
        while piece<end:
            here=piece.astimezone(zone)
            midnight=datetime.combine(here.date()+timedelta(days=1),time(),zone).astimezone(timezone.utc);stop=min(end,midnight)
            index=(here.date()-first).days
            if 0<=index<days:
                a=here.hour*60+here.minute+here.second/60;b=stop.astimezone(zone)
                rows[index].append((a,1440 if stop==midnight else max(a,b.hour*60+b.minute+b.second/60),text))
            piece=stop
    left,right,top=70,630,22;row=30 if days<=14 else max(3,min(30,420/days));every=ceil(16/row)
    height=top+days*row+4;e=escape
    out=[f'<svg viewBox="0 0 660 {height:.0f}" width="100%" role="img" aria-label="{e(label,quote=True)}"><g font-size="12" fill="#6e6a86">']
    for hour in (0,6,12,18,24):
        x=left+(right-left)*hour/24
        out.append(f'<text x="{x:.1f}" y="14" text-anchor="middle">{e(t["hour_label"].format(hour=hour))}</text>'
                   f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{top}" y2="{height-4:.1f}" stroke="#e6ddd3"/>')
    for index in range(0,days,every):
        out.append(f'<text x="0" y="{top+index*row+min(row,30)/2+4:.1f}">{e(day_text(t,first+timedelta(days=index)))}</text>')
    out.append('</g><g fill="#4f6d8a">')
    # Tall rows leave a gap between days; short rows (long periods) use the whole row so lines stay continuous.
    line_y,line_h=(row*.2,row*.6) if row>=10 else (0,row)
    for index,lines in enumerate(rows):
        y=top+index*row
        out.append(f'<g class="day"><rect class="band" x="{left-8}" y="{y:.1f}" width="{right-left+16}" height="{row:.1f}"/>')
        out.extend(f'<rect x="{left+(right-left)*a/1440:.1f}" y="{y+line_y:.1f}" width="{max(1,(right-left)*(b-a)/1440):.2f}" height="{line_h:.1f}" data-label="{e(text,quote=True)}"/>'
                   for a,b,text in lines)
        out.append('</g>')
    out.append('</g></svg>')
    return ''.join(out)


def create_html(folder,manifest,snapshots,evidence,quiz,records=()):
    """A portable file:// report: no network, fetch, framework or embedded audio blobs."""
    content=blocks(manifest,snapshots,quiz,evidence)
    t=messages(manifest['locale']);e=escape;plain=[];table_number=0
    def lines(text):return e(str(text)).replace('\n','<br>')
    def parts(cell):
        if not isinstance(cell,list):return lines(cell),str(cell).replace('\n',' ')
        html=[];text=[]
        for part in cell:
            kind=part[0]
            if kind=='br':html.append('<br>');continue
            value=part[-1];text.append(value)
            if kind=='a':html.append(f'<a href="{e(part[1],quote=True)}">{e(value)}</a>')
            elif kind=='small':html.append(f'<small>{e(value)}</small>' if value else '')
            elif kind.startswith('badge-'):html.append(f'<span class="badge {kind[6:]}">{e(value)}</span>')
            else:html.append(e(value))
        return ''.join(html),' '.join(v.strip() for v in text if v)
    def render(block):
        nonlocal table_number
        kind=block['type']
        if kind=='section':
            tag='h1' if block.get('level')==1 else 'h2';plain.append(block['title']+(' '+block['subtitle'] if block.get('subtitle') else ''))
            head=f'<{tag}>{e(block["title"])}'+(f' <span class="muted">{e(block["subtitle"])}</span>' if block.get('subtitle') else '')+f'</{tag}>'
            body=''.join(render(b) for b in block['body'])
            if 'figures' in block:
                figures=''.join(f'<figure id="{e(f["id"])}"><a class="figure-link" href="{e(f["path"],quote=True)}" aria-label="{e(t["save_figure"],quote=True)}">'
                    f'<img loading="lazy" width="1920" height="2500" src="{e(f["path"],quote=True)}" alt="{e(f["alt"],quote=True)}"></a>'
                    +(f'<figcaption>{e(f["caption"])}</figcaption>' if f['caption'] else '')
                    +f'<p class="back" data-nocopy><a href="{e(f["path"],quote=True)}" download>{e(t["save_figure"])}</a></p></figure>' for f in block['figures'])
                body=f'<div class="species"><div>{figures or "<p class=nofig>"+e(block["nofigure"])+"</p>"}</div><div>{body}</div></div>'
                if not block['figures']:plain.append(block['nofigure'])
            return f'<section class="block" id="{e(block["id"])}">{head}{body}</section>'
        if kind=='heading':
            text=block['text']+(block['badge'][1] if block.get('badge') else '')+block.get('after','');plain.append(text)
            badge=f'<span class="badge {block["badge"][0]}">{e(block["badge"][1])}</span>' if block.get('badge') else ''
            ident=f' id="{e(block["id"])}"' if block.get('id') else ''
            return f'<h3{ident}>{e(block["text"])}{badge}{e(block.get("after",""))}</h3>'
        if kind=='paragraph':
            plain.append(block['text']);cls=' '.join(c for c in (block.get('cls'),'muted' if block.get('muted') else '') if c)
            return (f'<p class="{cls}">' if cls else '<p>')+lines(block['text'])+'</p>'
        if kind=='tiles':
            plain.append('\n'.join(f'{value} {label}' for value,label in block['items']))
            return '<div class="tiles">'+''.join(f'<div class="tile"><b>{e(value)}</b>{e(label)}</div>' for value,label in block['items'])+'</div>'
        if kind=='facts':
            plain.append('\n'.join(f'{k}: {v}'.replace('\n',' / ') for k,v in block['items']))
            return '<dl>'+''.join(f'<dt>{e(k)}</dt><dd>{lines(v)}</dd>' for k,v in block['items'])+'</dl>'
        if kind=='legend':
            plain.append('\n'.join(text for _,text in block['items']))
            return '<p class="legend">'+''.join(f'<span><i class="swatch" style="background:{color}"></i>{e(text)}</span>' for color,text in block['items'])+'</p>'
        if kind=='sounds':
            # One native player is created on demand by report.js, even for thousands of saved answers.
            items=[]
            for item in block['items']:
                control=(f'<button class="play" type="button" data-audio="{e(item["audio"],quote=True)}" data-play="{e(t["play"],quote=True)}" data-pause="{e(t["pause"],quote=True)}" '
                         f'aria-label="{e(item["label"],quote=True)}" aria-pressed="false">{e(t["play"])}</button>' if item['audio'] else f'<span class="muted">{e(t["audio_unavailable"])}</span>')
                items.append(f'<li>{control}<span>{e(item["text"])}</span><span class="muted">{e(item["note"])}</span></li>')
            return '<ul class="sounds" data-nocopy>'+''.join(items)+'</ul>'
        if kind=='details':
            plain.append(block['summary']);ident=f' id="{e(block["id"])}"' if block.get('id') else ''
            return f'<details{ident}><summary>{e(block["summary"])}</summary>'+''.join(render(b) for b in block['body'])+'</details>'
        if kind=='links':
            return '<p class="back" data-nocopy>'+t['link_separator'].join(f'<a href="{e(href,quote=True)}">{e(text)}</a>' for href,text in block['items'])+'</p>'
        if kind=='schedule':
            plain.append(block['text'])
            return f'<div class="schedule">{schedule_svg(records,manifest,t,block["label"])}<div class="tip" role="status" hidden></div></div><p>{lines(block["text"])}</p>'
        if kind=='table':
            table_number+=1
            def table(rows,ident,hidden=False,labelledby=None):
                attrs=(f' tab-panel" role="tabpanel" aria-labelledby="{labelledby}" tabindex="0"' if labelledby else '" role="region" tabindex="0"'
                       f' aria-label="{e(block["headers"][0],quote=True)}"')
                html=[f'<div class="table-scroll{attrs} id="{ident}"{" hidden" if hidden else ""}><table><thead><tr>',
                      ''.join(f'<th scope="col">{e(h)}</th>' for h in block['headers']),'</tr></thead><tbody>']
                plain.append('\t'.join(block['headers']))
                for row in rows:
                    cells=[parts(cell) for cell in row];plain.append('\t'.join(text for _,text in cells))
                    html.append('<tr>'+''.join(f'<td>{cell}</td>' for cell,_ in cells)+'</tr>')
                return ''.join(html)+'</tbody></table></div>'
            tabs=block.get('tabs')
            if not tabs:return table(block['rows'],f'table-{table_number}')
            html=[f'<div class="tabs" role="tablist" aria-label="{e(t["col_model"],quote=True)}">']
            for index,label in enumerate(tabs):
                html.append(f'<button type="button" role="tab" id="tab-{table_number}-{index}" aria-controls="table-{table_number}-{index}" '
                            f'aria-selected="{str(index==0).lower()}" tabindex="{0 if index==0 else -1}">{e(label)}</button>')
            html.append('</div>')
            html+=[table(rows,f'table-{table_number}-{index}',index>0,f'tab-{table_number}-{index}') for index,rows in enumerate(tabs.values())]
            return ''.join(html)
        raise ValueError(kind)
    main=''.join(render(block) for block in content)
    css=(DATA/'report.css').read_text(encoding='utf-8')
    script=(DATA/'report.js').read_text(encoding='utf-8')
    nav=''.join(f'<a href="#{key}">{e(t["nav_"+key])}</a>' for key in ('summary','species','recordings','details'))
    html=f'''<!doctype html><html lang="{manifest['locale']}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' file: data:; media-src 'self' file:; style-src 'unsafe-inline'; script-src 'unsafe-inline'">
<title>{e(t['title'])} · {e(manifest['site']['name'])}</title><style>{css}</style></head><body>
<header><img src="figures/echoroo.png" width="32" height="32" alt=""><b>Echoroo Lite</b><nav aria-label="{e(t['nav_label'],quote=True)}">{nav}</nav><span class="stamp">{e(manifest['site']['name'])}{e(t['dot'])}{e(period_text(t,manifest))}</span></header>
<div class="tools"><button id="copy" type="button" data-success="{e(t['copied'],quote=True)}" data-failure="{e(t['copy_failed'],quote=True)}">{e(t['copy'])}</button><p id="copy-status" role="status"></p><p>{e(t['copy_hint'])}</p></div>
<main>{main}</main><textarea hidden id="plain-text">{e(chr(10).join(plain))}</textarea><footer>{e(t['saved']+t['colon']+saved_time(manifest))}</footer><script>{script}</script></body></html>'''
    (folder/'index.html').write_text(html,encoding='utf-8')
    (folder/'report.txt').write_text('\n\n'.join(plain),encoding='utf-8')
    (folder/'report-content.json').write_text(json.dumps(content,ensure_ascii=False,indent=2),encoding='utf-8')
