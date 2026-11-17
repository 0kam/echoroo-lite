import unittest
from copy import deepcopy

from report_document import ai_state, blocks, duration_text, messages, schedule_svg, status, status_text
from workflow_export import quality_export


def snapshot(state='decided', rare=False):
    metric={'value':.5,'low':.3,'high':.7}
    result={'status':state,'threshold':.4 if state=='decided' else None,'density':metric,'count':{'value':50,'low':30,'high':70},
        'precision':metric if state=='decided' else None,'recall':None if rare else metric,'recall_status':'not_estimable_rare' if rare else 'estimated',
        'auprc':metric,'answers_per_bin':[],'unknown':2,'disagreement':True,'reason':'target_unreachable' if state=='undecided' else None}
    validation={'id':7,'applicable':True,'stage':'complete','method':'stratified_quantile6_v1','status':state,
        'thresholds':{'birdnet':{'threshold':result['threshold'],'reason':'target_unreachable' if state=='undecided' else None}},
        'results':{'birdnet':result},'counts':{'total':60,'answered':60,'unknown':2,'bins':[{'heard':14,'not_heard':44,'unknown':2}]}}
    species={'id':'sp','scientific_name':'Example bird','common_names':{'ja':'例鳥','en':'Example bird'}}
    return {'kind':'detection','engine':'birdnet','model_sha256':'model','threshold':result['threshold'],'window_s':3,
        'species':species,'summaries':[{'species':species,'candidate_windows':4,'eligible_windows':100,'candidate_rate':.04,'candidate_days':2}],
        'validation':validation,'figure_id':'F001'}


def manifest(snap, locale='ja'):
    return {'locale':locale,'site':{'name':'Site','timezone':'Asia/Tokyo','latitude':35,'longitude':138},
        'start_date':'2024-01-01','end_date':'2024-01-02','scope':{'recording_ids':[1],'recorded_seconds':60,
        'daily':[{'seconds':60}],'schedule':[],'analysed_seconds':{'BirdNET':60}},'presence':[{'species_id':'sp','status':'confirmed',
        'evidence_count':1,'listened_without_confirming':None}],'selected_species':[snap['species']],
        'file_guide':[],'saved_at':'2024-01-01T00:00:00+00:00','package_id':'p','schema':'s'}


def sections(content):
    return {block['id']:block for block in content if block['type']=='section'}


class ReportDocumentTest(unittest.TestCase):
    def test_new_statuses(self):
        self.assertEqual(status(snapshot()),'decided')
        undecided=snapshot('undecided');self.assertEqual(status(undecided),'undecided')
        undecided['validation']['stage']='sample';self.assertEqual(status(undecided),'in_progress')
        undecided['validation']['applicable']=False;self.assertEqual(status(undecided),'stale')

    def test_undecided_check_is_not_used_and_names_its_reason(self):
        for locale in ('ja','en'):
            t=messages(locale);snap=snapshot('undecided');snap['threshold']=None
            snap['summaries'][0].update(candidate_windows=None,candidate_rate=None,candidate_days=None)
            self.assertEqual(ai_state(snap),'unused')
            self.assertEqual(status_text(snap,t),t['undecided'])
            report=manifest(snap,locale);report['presence'][0]['counting_status']='undecided'
            content=blocks(report,[snap],{'per_species':[]});rendered=str(content)
            self.assertEqual(sections(content)['sp-sp']['figures'],[])
            for text in (t['unused_other'].format(name=snap['species']['common_names'][locale]),t['badge_unused'],t['source_none'],t['uncounted']):
                self.assertIn(text,rendered)
            self.assertNotIn(t['rule_default'].split('{')[0],rendered)
            snap['validation']['results']['birdnet']['reason']='no_target'
            self.assertEqual(status_text(snap,t),t['undecided_no_target'])
            rendered=str(blocks(report,[snap],{'per_species':[]}))
            self.assertIn(t['unused_no_target'].format(name=snap['species']['common_names'][locale]),rendered)
            self.assertIn(t['table_unused_no_target'],rendered)

    def test_unfinished_or_stale_checks_are_reference(self):
        t=messages('ja')
        for stage,applicable,key in (('sample',True,'precision_in_progress'),('complete',False,'precision_stale')):
            snap=snapshot();snap['validation'].update(stage=stage,applicable=applicable)
            self.assertEqual(ai_state(snap),'reference')
            rendered=str(blocks(manifest(snap),[snap],{'per_species':[]}))
            self.assertIn(t[key],rendered);self.assertIn(t['badge_reference'],rendered)

    def test_details_keep_uncertainty_without_score_groups(self):
        snap=snapshot(rare=True)
        for locale in ('ja','en'):
            t=messages(locale);rendered=str(blocks(manifest(snap,locale),[snap],{'per_species':[]}))
            for text in (t['rare_recall'],t['disagreement'],t['answered_text'].format(answered=60,heard=14,not_heard=44,unknown=2)):
                self.assertIn(text,rendered)
            self.assertNotIn('AUPRC',rendered);self.assertNotIn('5,000.0%',rendered)

    def test_unusual_species_is_noted_and_method_moves_to_readme(self):
        snap=snapshot()
        for locale in ('ja','en'):
            report=manifest(snap,locale);report['presence'][0]['region']='unusual'
            rendered=str(blocks(report,[snap],{'per_species':[]}))
            self.assertIn(messages(locale)['region_unusual_note'],rendered)
            self.assertNotIn(messages(locale)['region_source'],rendered)

    def test_decided_check_states_threshold_and_precision(self):
        snap=snapshot();t=messages('en')
        rendered=str(blocks(manifest(snap,'en'),[snap],{'per_species':[]}))
        self.assertIn('Of the clips counted as "call present", about 50% were correct (probably between 30% and 70%; estimated from 60 sounds that people listened to)',rendered)
        self.assertIn(t['rule_checked'].format(score='0.40'),rendered)
        self.assertIn('4 clips (4.0% of the 100 clips examined). Days with detections: 2',rendered)

    def test_summary_states_how_sure_and_durations_read_as_words(self):
        snap=snapshot();t=messages('ja');report=manifest(snap);report['other_species']={'candidates':18,'heard':0}
        report['scope']['recorded_seconds']=2400
        summary=str(sections(blocks(report,[snap],{'per_species':[]}))['summary'])
        self.assertIn('人が聞いて声を確かめたのは、例鳥です。AI の精度まで確かめたのは、例鳥（正しかったのは約 50%）です。',summary)
        self.assertIn('録音 40 分',summary);self.assertIn(t['other_candidates'].format(count=18)+t['other_include'],summary)
        self.assertNotIn(t['other_heard'].split('{')[0],summary)
        report['other_species']={'candidates':0,'heard':2}
        summary=str(sections(blocks(report,[snap],{'per_species':[]}))['summary'])
        self.assertIn(t['other_heard'].format(count=2)+t['other_include'],summary);self.assertNotIn('AI の候補になったが',summary)
        reading=str(sections(blocks(report,[snap],{'per_species':[]}))['species'])
        self.assertIn('BirdNET 2.4 は 3 秒',reading);self.assertNotIn('5 秒',reading)
        self.assertEqual([duration_text(s,t) for s in (5400,43200,1200)],['1 時間 30 分','12 時間','20 分'])
        self.assertEqual(duration_text(5400,messages('en')),'1 h 30 min')
        snap['validation']['stage']='sample';snap['species']['common_names']={};snap['species']['custom_names']={'ja':'自作名'}
        summary=str(sections(blocks(manifest(snap,'en'),[snap],{'per_species':[]}))['summary'])
        self.assertIn('The AI accuracy check for 自作名 is not finished',summary);self.assertNotIn('other species',summary)

    def test_quality_csv_has_new_metrics_without_old_goal_fields(self):
        snap=snapshot();result=snap['validation']['results']['birdnet']
        result['multiplier']={'value':123,'low':100,'high':150}
        row=quality_export(snap['validation'],result)
        self.assertEqual((row['density_value'],row['precision_value'],row['unknown'],row['disagreement']),(.5,.5,2,True))
        self.assertNotIn('goal_met',row);self.assertNotIn('precision_lower_95',row)
        self.assertFalse(any(key.startswith('multiplier') for key in row))
        for locale in ('ja','en'):
            self.assertNotIn('multiplier',messages(locale))
            rendered=str(blocks(manifest(snap,locale),[snap],{'per_species':[]}))
            self.assertNotIn('123.00',rendered)

    def test_species_with_evidence_come_first_and_heard_sounds_exclude_other_answers(self):
        confirmed=snapshot();not_found=deepcopy(confirmed);unchecked=deepcopy(confirmed)
        for snap,sid,figure in ((unchecked,'unchecked','F001'),(not_found,'not-found','F002'),(confirmed,'confirmed','F003')):
            snap['species']={**snap['species'],'id':sid,'scientific_name':sid,'common_names':{'ja':sid,'en':sid}}
            snap['summaries'][0]['species']=snap['species'];snap['figure_id']=figure
        report=manifest(confirmed)
        report['selected_species']=[snap['species'] for snap in (unchecked,not_found,confirmed)]
        report['presence']=[
            {'species_id':'unchecked','status':'unchecked','evidence_count':0,'listened_without_confirming':None},
            {'species_id':'not-found','status':'not_found','evidence_count':0,'listened_without_confirming':10},
            {'species_id':'confirmed','status':'confirmed','evidence_count':7,'listened_without_confirming':None}]
        heard=[{'species_id':'confirmed','audio_role':'presence','origin':'mark','audio_file':f'audio/{i}.wav','start_s':i,'end_s':i+5,
                'local_time':f'2024-01-01T06:0{i}:00+09:00'} for i in range(7)]
        other={'species_id':'confirmed','audio_role':'validation','validation_id':7,'verdict':'other','audio_file':'audio/x.wav',
               'start_s':0,'end_s':3,'local_time':'2024-01-01T05:00:00+09:00'}
        t=messages('ja')
        content=blocks(report,[unchecked,not_found,confirmed],{'per_species':[]},heard+[other])
        ids=[block['id'] for block in content if block['type']=='section']
        self.assertEqual(ids,['summary','species','sp-confirmed','sp-unchecked','sp-not-found','recordings','details'])
        self.assertTrue(all(sections(content)[key]['figures'] for key in ('sp-confirmed','sp-unchecked','sp-not-found')))
        body=sections(content)['sp-confirmed']['body']
        shown=[block for block in body if block['type']=='sounds'][0]['items']
        self.assertEqual([item['text'] for item in shown],[f'1月1日 6:0{i}:00' for i in range(5)])
        more=[block for block in body if block['type']=='details'][0]
        self.assertEqual(more['summary'],t['heard_more'].format(count=2))
        self.assertNotIn('audio/x.wav',str(body))
        self.assertIn('audio/x.wav',str(sections(content)['details']))
        self.assertIn(t['not_found_text'].format(count=10),str(content))
        summaries=[block['summary'] for block in sections(content)['details']['body'] if block['type']=='details']
        self.assertNotIn(t['detail_quiz'],summaries)
        quiz={'per_species':[{'species_id':'confirmed','correct':1,'total':2,'unknown':0,'answered_at':'2024-01-01T00:00:00+00:00',
                              'session_id':3,'complete':True,'set_sha256':'abc'}]}
        summaries=[block['summary'] for block in sections(blocks(report,[confirmed],quiz))['details']['body'] if block['type']=='details']
        self.assertIn(t['detail_quiz'],summaries)

    def test_custom_ai_date_uses_site_zone_and_shared_length_is_said_once(self):
        t=messages('ja');snap=snapshot();snap['window_s']=5
        custom=deepcopy(snap);custom.update(engine='custom',created_at='2026-11-16 15:24:00',site={'timezone':'Asia/Tokyo'},figure_id='F002',
                      custom_model={'training':{'positive_5s':21,'negative_5s':20}})
        content=blocks(manifest(snap),[snap,custom],{'per_species':[{'species_id':'sp','correct':0,'total':1,'unknown':1,
            'answered_at':'2026-11-16T15:29:00+00:00','session_id':2,'complete':False}]})
        rendered=str(content);reading=str(sections(content)['species'])
        self.assertIn('この種用に作ったAI（11/17作成）',rendered);self.assertNotIn('11/16',rendered)
        self.assertIn('このレポートの AI は、どれも 5 秒',reading);self.assertIn(t['reading_no_sum'],reading)
        self.assertIn('2 回目・途中でやめた',rendered);self.assertIn('2026年11月17日 0:29',rendered)
        self.assertIn('「声あり」の音 21、「声なし」の音 20',rendered)

    def test_third_walkthrough_wording(self):
        t=messages('ja');snap=snapshot();snap['validation'].update(stage='sample',results={})
        other=deepcopy(snap);other.update(engine='perch',window_s=5,figure_id='F002',validation=None)
        other['summaries'][0].update(candidate_windows=0,candidate_rate=0,candidate_days=0)
        lone=deepcopy(snap);lone['species']={**lone['species'],'id':'lone','scientific_name':'Lone bird','common_names':{'ja':'孤鳥'}}
        lone['summaries'][0]['species']=lone['species'];lone['figure_id']='F003'
        report=manifest(snap);report['selected_species'].append(lone['species'])
        report['presence'].append({'species_id':'lone','status':'not_found','evidence_count':0,'listened_without_confirming':10})
        report['other_species']={'candidates':1,'heard':0,'candidate_species':[{'species':lone['species'],'windows':[['BirdNET 2.4',12]]}]}
        content=blocks(report,[snap,other,lone],{'per_species':[]});summary=str(sections(content)['summary'])
        # Every AI line names its AI once the report has two AIs, even for a species with one result.
        rows=sections(content)['summary']['body'][3]['rows']
        self.assertTrue(all(cell[0]=='text' and cell[1].startswith(('BirdNET 2.4','Perch')) for row in rows for cell in row[2] if cell[0]=='text'))
        self.assertIn(t['table_found_none'],summary);self.assertIn(t['other_list_title'].format(count=1),summary);self.assertIn('BirdNET 2.4：12 音',summary)
        self.assertIn('AI の点数が高い順に10音を聞いたが、声を確かめられなかった',str(content))
        report['presence'][1]['not_found_list']='search'
        self.assertIn('お手本に似ている順に10音を聞いたが',str(blocks(report,[snap,other,lone],{'per_species':[]})))
        # A check with no answers yet reads as not started.
        unanswered=snapshot();unanswered['validation'].update(stage='sample',results={});unanswered['validation']['counts']['answered']=0
        self.assertEqual(status(unanswered),'not_evaluated')
        rendered=str(blocks(manifest(unanswered),[unanswered],{'per_species':[]}))
        self.assertIn(t['precision_unknown'],rendered);self.assertNotIn(t['precision_in_progress'],rendered);self.assertIn(t['checks_none'],rendered)
        species=str(sections(content)['sp-sp']);self.assertIn(t['count_none'].format(eligible=100,name='例鳥'),species)
        facts=[block['items'] for block in sections(content)['sp-sp']['body'] if block['type']=='facts']
        self.assertEqual([[k for k,_ in items].count(t['fact_precision']) for items in facts],[1,0])
        # The range note only accompanies a shown range.
        self.assertNotIn(t['range_note'],str(content))
        self.assertIn(t['range_note'],str(blocks(manifest(snapshot()),[snapshot()],{'per_species':[]})))

    def test_fourth_walkthrough_wording(self):
        from workflow_export import folder_name
        t=messages('ja');snap=snapshot();snap['threshold']=.1;snap['validation'].update(stage='sample',results={})
        snap['validation']['counts'].update(answered=5,bins=[{'heard':0,'not_heard':0,'unknown':5}])
        report=manifest(snap);report['presence'][0].update(status='unchecked',evidence_count=0)
        answers=[{'species_id':'sp','audio_role':'validation','validation_id':7,'verdict':'unknown','audio_file':f'audio/{i}.wav',
                  'start_s':i,'end_s':i+3,'local_time':'2024-01-01T05:00:00+09:00'} for i in range(5)]
        rendered=str(blocks(report,[snap],{'per_species':[]},answers))
        # Answers that confirmed nothing are not "not listened yet", and the play link names what is inside.
        self.assertIn('精度の確認で 5 音を聞いたが、まだ声を確かめられていない（すべて「判断できない」）',rendered)
        self.assertNotIn(t['badge_unchecked'],rendered)
        self.assertIn('答えた 5 音を聞く（すべて「判断できない」）',rendered)
        # The method says which cut-off each result used.
        self.assertIn(t['method_cutoff_default_all'],rendered);self.assertNotIn('の境目は、その答えから決めました',rendered)
        rendered=str(blocks(manifest(snapshot()),[snapshot()],{'per_species':[]}))
        self.assertIn('例鳥（BirdNET 2.4）の境目は、その答えから決めました',rendered);self.assertNotIn(t['method_cutoff_default_rest'],rendered)
        self.assertEqual(folder_name('里山の森','2025-07-11','2025-07-13','d3748e'),'里山の森_2025-07-11〜13_d3748e')
        self.assertEqual(folder_name(' a<b>:c/d ','2024-12-30','2025-01-02','x'),'a_b__c_d_2024-12-30〜2025-01-02_x')

    def test_schedule_draws_each_recording_at_its_local_time(self):
        t=messages('ja');report=manifest(snapshot())
        crossing={'recorded_at':'2023-12-31T14:50:00+00:00','seconds':1200}
        svg=schedule_svg([crossing],report,t,'label')
        # Only the part inside the period is drawn; the label keeps the recording's own start.
        self.assertEqual(svg.count('data-label="12月31日 23:50〜0:10（20 分）"'),1)
        crossing['recorded_at']='2024-01-01T14:50:00+00:00'
        svg=schedule_svg([crossing],report,t,'label')
        self.assertEqual(svg.count('data-label="1月1日 23:50〜0:10（20 分）"'),2)
        year={**report,'end_date':'2024-12-30'}
        svg=schedule_svg([{'recorded_at':f'2024-{m:02d}-01T00:00:00+09:00','seconds':300} for m in range(1,13)],year,t,'label')
        self.assertEqual(svg.count('class="day"'),365);self.assertEqual(svg.count('data-label'),12)


if __name__=='__main__':unittest.main()
