import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path

from report_document import blocks, create_html, criterion, status, status_text, messages
from workflow_export import quality_export


def snapshot(method='fixed_precision_audit_v1', stage='complete', precision_complete=True):
    result={
        'precision': {'value': .93, 'low': .78, 'high': .99} if precision_complete else None,
        'recall': None, 'estimated_window_rate': None, 'precision_lower_95': .804 if precision_complete else None,
        'precision_complete': precision_complete, 'recall_complete': False, 'goal_met': precision_complete,
        'precision_sample': {'total': 30, 'answered': 30 if precision_complete else 12, 'target': 28 if precision_complete else 11, 'unknown': 0, 'population': 120},
        'recall_sample': {'total': 0, 'answered': 0, 'target': 0, 'unknown': 0, 'population': 90},
        'eligible_windows': 120, 'reviewed': 30 if precision_complete else 12, 'unknown': 0,
    }
    validation={'id': 7, 'applicable': True, 'stage': stage, 'method': method, 'target_precision': .8, 'split_unit': 'local_day',
        'calibration_target_precision': .9, 'evaluation_scope': 'fixed_species_model_threshold_site_period',
        'sampling_unit': 'non_overlapping_15_second_interval', 'reference': 'user_annotations',
        'counts': {'calibration': {'answered': 10}, 'evaluation': {'answered': 30}},
        'results': {'birdnet': result}, 'thresholds': {'birdnet': {'adjusted': True}}, 'evaluated_count': 30}
    return {'kind': 'detection', 'engine': 'birdnet', 'model_sha256': 'model', 'threshold': .5, 'window_s': 15,
        'species': {'id': 1, 'scientific_name': 'Testus birdus', 'common_names': {'ja': 'テスト鳥', 'en': 'Test bird'}},
        'summaries': [{'candidate_windows': 20, 'eligible_windows': 120, 'candidate_rate': 1/6, 'candidate_days': 2}],
        'cells': [], 'evidence': [], 'figure_id': 'F001', 'validation': validation}


def manifest(snaps, locale='ja', presence=None):
    species=[snaps[0]['species']] if snaps else []
    return {'locale': locale, 'site': {'name': 'Site', 'latitude': 35., 'longitude': 138., 'timezone': 'Asia/Tokyo'},
        'start_date': '2026-01-01', 'end_date': '2026-01-02', 'selected_species': species,
        'presence': presence if presence is not None else [{'species_id': s['id'], 'status': 'unchecked', 'evidence_count': 0, 'listened_without_confirming': None} for s in species],
        'scope': {'recording_ids': [1], 'recorded_seconds': 30., 'daily': [{'date': '2026-01-01', 'seconds': 30.}],
            'schedule': [{'date': '2026-01-01', 'hours': [30.] + [0.] * 23}], 'analysed_seconds': {'BirdNET': 30., 'Perch': 30.}},
        'saved_at': '2026-01-03T00:00:00+00:00', 'package_id': 'test', 'schema': 'test', 'file_guide': []}


def text(report):
    return '\n'.join(str(block) for block in report)


class ReportDocumentTest(unittest.TestCase):
    def test_completed_precision_stays_visible_in_both_locales_while_recall_is_underway(self):
        snap=snapshot(stage='evaluation')
        snap['validation']['results']['birdnet']['recall_sample'].update(total=30,answered=4)
        self.assertEqual(status(snap),'goal_met')
        for locale, expected in [('ja', '目標判定用の片側95%下限 80.4%'), ('en', 'one-sided 95% lower bound for the goal: 80.4%')]:
            report=text(blocks(manifest([snap], locale), [snap], {'per_species': []}))
            self.assertIn(expected, report)
            self.assertIn(messages(locale)['recall_not_established'], report)
            self.assertIn(messages(locale)['fixed_precision_scope'], report)

    def test_completed_recall_shows_its_range_and_sample_in_plain_words(self):
        snap=snapshot()
        result=snap['validation']['results']['birdnet']
        result.update(recall={'value': .04, 'low': .007, 'high': .71}, recall_complete=True)
        result['recall_sample'].update(total=30, answered=30, target=1, population=3835)
        for locale, expected in [('ja', ['約4.0%（0.7%〜71.0%のどこか。聞いた音が少ないため幅が広い）', '約93.0%（78.0%〜99.0%のどこか）',
                'AIが「いない」とした3,835区間のうち30区間を聞き、1区間で声がありました。']),
            ('en', ['about 4.0% (somewhere between 0.7% and 71.0%; wide because few sounds were checked)',
                'Of the 3,835 intervals the AI marked as “absent”, 30 were checked and 1 had the call.'])]:
            report=text(blocks(manifest([snap], locale), [snap], {'per_species': []}))
            for part in expected:self.assertIn(part, report)

    def test_pending_calibrating_and_undecided_states(self):
        pending=snapshot(stage='evaluation', precision_complete=False)
        self.assertEqual(status(pending), 'pending')
        self.assertIn('no decision yet', status_text(pending, messages('en')))
        calibrating=snapshot(stage='calibration');calibrating['validation']['results']={}
        self.assertEqual(status(calibrating), 'calibrating')
        undecided=snapshot();undecided['threshold']=None
        undecided['validation']['results']={'birdnet':{'threshold':None,'reason':'no_target','status':'undecided','goal_met':False,'precision_complete':False}}
        undecided['validation']['thresholds']={'birdnet':{'threshold':None,'adjusted':False,'reason':'no_target'}}
        undecided['summaries']=[{'candidate_windows': None, 'eligible_windows': 120, 'candidate_rate': None, 'candidate_days': None}]
        self.assertEqual(status(undecided), 'undecided')
        for locale,expected in [('ja','基準を決められなかった'),('en','No threshold could be set')]:
            self.assertEqual(status_text(undecided,messages(locale)),expected)
            self.assertIn(expected,criterion(undecided,messages(locale)))
            report=text(blocks(manifest([undecided],locale),[undecided],{'per_species':[]}))
            self.assertIn(messages(locale)['uncounted'].format(eligible='120'),report)
            self.assertIn(messages(locale)['activity_none'],report)
        unchecked=snapshot();unchecked['validation']=None
        self.assertEqual(criterion(unchecked,messages('ja')).splitlines()[-1],messages('ja')['default'])

    def test_presence_and_time_columns_per_species(self):
        met=snapshot();other=deepcopy(met);other['species']={**other['species'],'id':2,'common_names':{'ja':'別の鳥','en':'Other bird'}}
        other['validation']=None
        base=manifest([met],'ja',[{'species_id':1,'status':'confirmed','evidence_count':3,'listened_without_confirming':None},
                                  {'species_id':2,'status':'not_found','evidence_count':0,'listened_without_confirming':12}])
        base['selected_species']=[met['species'],other['species']]
        report=blocks(base,[met,other],{'per_species':[]})
        table=next(block for block in report if block['type']=='table' and block['headers'][1]=='いるか')
        self.assertEqual([row[1:] for row in table['rows']],[['聞いて確認した（根拠の音 3）','確かな図あり（BirdNET）'],
                                                              ['上位12音を聞いたが確認できなかった','確かな図なし']])
        self.assertIn('聞いて、その種の声だと確かめた種は1種でした。',text(report))
        self.assertIn('時間ごとの数え方の正しさが目標に届いた種は1種でした。',text(report))
        base['presence'][1]['status']='unchecked'
        en=blocks({**base,'locale':'en'},[met,other],{'per_species':[]})
        table=next(block for block in en if block['type']=='table' and block['headers'][1]=='Present')
        self.assertEqual(table['rows'][1][1:],['Not checked','No reliable plot'])

    def test_quality_csv_fields_separate_decision_bound_and_descriptive_interval(self):
        snap=snapshot(stage='evaluation');validation=snap['validation'];result=validation['results']['birdnet']
        result['recall_sample'].update(total=30,answered=4)
        row=quality_export(validation,result)
        self.assertEqual(row['precision_lower_95'], .804)
        self.assertEqual((row['precision_low'],row['precision_high']),(.78,.99))
        self.assertTrue(row['goal_met'])
        self.assertIsNone(row['recall_value'])
        self.assertEqual(row['precision_sample_population'],120)
        self.assertFalse(row['recall_complete'])
        self.assertEqual((row['target_precision'],row['calibration_target_precision']),(.8,.9))
        pending=snapshot(stage='evaluation',precision_complete=False)
        pending_result=pending['validation']['results']['birdnet']
        pending_result['precision']={'value': .92, 'low': .7, 'high': 1.}
        pending_result['precision_lower_95']=.75
        row=quality_export(pending['validation'],pending_result)
        self.assertIsNone(row['precision_value'])
        self.assertIsNone(row['precision_lower_95'])

    def test_species_and_quality_tables_are_grouped_by_model(self):
        birdnet=snapshot();birdnet['species']['id']='test-species';perch=deepcopy(birdnet);perch['engine']='perch';perch['model_sha256']='perch'
        perch['validation']['models']={'perch':'perch'}
        perch['validation']['results']={'perch':perch['validation']['results'].pop('birdnet')}
        perch['validation']['thresholds']={'perch':{'adjusted':True}}
        base=manifest([birdnet],'en')
        tables=[block for block in blocks(base,[birdnet,perch],{'per_species':[]}) if block['type']=='table' and block.get('tabs')]
        self.assertGreaterEqual(len(tables),2)
        self.assertTrue(all(list(table['tabs'])==['BirdNET','Perch'] for table in tables[:2]))
        with TemporaryDirectory() as temporary:
            folder=Path(temporary)
            create_html(folder,base,[birdnet,perch],[],{'per_species':[]})
            html=(folder/'index.html').read_text(encoding='utf-8')
        self.assertIn('role="tablist"',html)
        self.assertIn('role="tabpanel" aria-labelledby="tab-',html)
        self.assertIn("['ArrowLeft','ArrowRight','Home','End']",html)
        self.assertIn('href="#presence"',html)


if __name__=='__main__':unittest.main()
