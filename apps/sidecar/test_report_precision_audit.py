import unittest
from copy import deepcopy
from tempfile import TemporaryDirectory
from pathlib import Path

from report_document import blocks, create_html, criterion, result_counts, status, status_text, messages
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
    validation={'id': 7, 'applicable': True, 'stage': stage, 'method': method, 'target_precision': .8,
        'calibration_target_precision': .9, 'evaluation_scope': 'fixed_species_model_threshold_site_period',
        'sampling_unit': 'non_overlapping_15_second_interval', 'reference': 'user_annotations',
        'counts': {'calibration': {'answered': 10}, 'evaluation': {'answered': 30}},
        'results': {'birdnet': result}, 'thresholds': {'birdnet': {'adjusted': True}}, 'evaluated_count': 30}
    return {'kind': 'detection', 'engine': 'birdnet', 'model_sha256': 'model', 'threshold': .5, 'window_s': 15,
        'species': {'id': 1, 'scientific_name': 'Testus birdus', 'common_names': {'ja': 'テスト鳥', 'en': 'Test bird'}},
        'summaries': [{'candidate_windows': 20, 'eligible_windows': 120, 'candidate_rate': 1/6, 'candidate_days': 2}],
        'cells': [], 'evidence': [], 'figure_id': 'F001', 'reference_ids': [], 'validation': validation}


class PrecisionAuditReportTest(unittest.TestCase):
    def test_completed_precision_stays_visible_in_both_locales_while_recall_is_underway(self):
        snap=snapshot(stage='evaluation')
        snap['validation']['results']['birdnet']['recall_sample'].update(total=30,answered=4)
        self.assertEqual(status(snap),'goal_met')
        base={'locale': 'ja', 'site': {'name': '地点', 'latitude': 35., 'longitude': 138., 'timezone': 'Asia/Tokyo'},
            'start_date': '2026-01-01', 'end_date': '2026-01-02', 'selected_species': [snap['species']],
            'manual_species': [], 'scope': {'recording_ids': [1], 'recorded_seconds': 30.,
                'daily': [{'date': '2026-01-01', 'seconds': 30.}], 'analysed_seconds': {'BirdNET': 30.}},
            'saved_at': '2026-01-03T00:00:00+00:00', 'package_id': 'test', 'schema': 'test', 'file_guide': []}
        quiz={'per_species': []}
        for locale, expected in [('ja', '目標判定用の片側95%下限 80.4%'), ('en', 'one-sided 95% lower bound for the goal: 80.4%')]:
            report=blocks({**base, 'locale': locale}, [snap], [], quiz)
            text='\n'.join(str(block) for block in report)
            self.assertIn(expected, text)
            self.assertIn(messages(locale)['recall_not_established'], text)
            self.assertIn(messages(locale)['fixed_precision_scope'], text)

    def test_pending_fixed_audit_has_no_interim_inference_and_legacy_is_unchanged(self):
        pending=snapshot(stage='evaluation', precision_complete=False)
        self.assertEqual(status(pending), 'pending')
        self.assertIn('no formal decision', status_text(pending, messages('en')))
        legacy=snapshot(method='stratified_finite_window_validation_v2')
        legacy['validation']['target_precision']=.9
        self.assertEqual(status(legacy), 'goal_met')
        self.assertEqual(status_text(legacy, messages('en')), 'Evidence supports at least 90%')

    def test_mixed_report_labels_sample_counts_in_both_locales(self):
        fixed=snapshot();legacy=snapshot(method='stratified_finite_window_validation_v2')
        legacy['validation']['results']['birdnet']['unknown']=2
        base={'site': {'name': 'Site', 'latitude': 35., 'longitude': 138., 'timezone': 'Asia/Tokyo'},
            'start_date': '2026-01-01', 'end_date': '2026-01-02', 'selected_species': [fixed['species']],
            'manual_species': [], 'scope': {'recording_ids': [1], 'recorded_seconds': 30.,
                'daily': [{'date': '2026-01-01', 'seconds': 30.}], 'analysed_seconds': {'BirdNET': 30.}},
            'saved_at': '2026-01-03T00:00:00+00:00', 'package_id': 'test', 'schema': 'test', 'file_guide': []}
        expected={'ja': ('確認した音の数','調整 10／評価 30／保留 2'),
            'en': ('Audio reviewed','calibration 10 / evaluation 30 / unresolved 2')}
        for locale,(header,legacy_counts) in expected.items():
            report=blocks({**base,'locale': locale},[fixed,legacy],[],{'per_species': []})
            text='\n'.join(str(block) for block in report)
            self.assertIn(header,text)
            self.assertIn(legacy_counts,text)

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
        self.assertEqual(row['target_precision'],.8)
        self.assertEqual(row['calibration_target_precision'],.9)
        pending=snapshot(stage='evaluation',precision_complete=False)
        pending_result=pending['validation']['results']['birdnet']
        pending_result['precision']={'value': .92, 'low': .7, 'high': 1.}
        pending_result['precision_lower_95']=.75
        row=quality_export(pending['validation'],pending_result)
        self.assertIsNone(row['precision_value'])
        self.assertIsNone(row['precision_lower_95'])

    def test_summary_and_unadjusted_checked_threshold_use_plain_separate_statements(self):
        checked=snapshot();unchecked=snapshot();unchecked['species']={**unchecked['species'],'id':2};unchecked['validation']=None
        self.assertEqual(result_counts([checked,unchecked]),(2,1,1))
        checked['validation']['thresholds']['birdnet']['adjusted']=False
        for locale,expected in [('ja','調整に必要な音が足りませんでした'),('en','not enough sounds')]:
            self.assertIn(expected,criterion(checked,messages(locale)))
            self.assertEqual(criterion(unchecked,messages(locale)).splitlines()[-1],messages(locale)['default'])

    def test_species_and_quality_tables_are_grouped_by_model(self):
        birdnet=snapshot();birdnet['species']['id']='test-species';perch=deepcopy(birdnet);perch['engine']='perch';perch['model_sha256']='perch'
        perch['validation']['models']={'perch':'perch'}
        perch['validation']['results']={'perch':perch['validation']['results'].pop('birdnet')}
        perch['validation']['thresholds']={'perch':{'adjusted':True}}
        base={'locale':'en','site':{'name':'Site','latitude':35.,'longitude':138.,'timezone':'Asia/Tokyo'},
            'start_date':'2026-01-01','end_date':'2026-01-01','selected_species':[birdnet['species']],
            'manual_species':[],'scope':{'recording_ids':[1],'recorded_seconds':30.,'daily':[{'date':'2026-01-01','seconds':30.}],
            'schedule':[{'date':'2026-01-01','hours':[30.]+[0.]*23}],'analysed_seconds':{'BirdNET':30.,'Perch':30.}},
            'saved_at':'2026-01-02T00:00:00+00:00','package_id':'test','schema':'test','file_guide':[]}
        tables=[block for block in blocks(base,[birdnet,perch],[],{'per_species':[]}) if block['type']=='table' and block.get('tabs')]
        self.assertGreaterEqual(len(tables),2)
        self.assertTrue(all(list(table['tabs'])==['BirdNET','Perch'] for table in tables[:2]))
        with TemporaryDirectory() as temporary:
            folder=Path(temporary)
            create_html(folder,base,[birdnet,perch],[],[],{'per_species':[]})
            html=(folder/'index.html').read_text(encoding='utf-8')
        self.assertIn('role="tablist"',html)
        self.assertIn('role="tabpanel" aria-labelledby="tab-',html)
        self.assertIn("['ArrowLeft','ArrowRight','Home','End']",html)


if __name__=='__main__':unittest.main()
