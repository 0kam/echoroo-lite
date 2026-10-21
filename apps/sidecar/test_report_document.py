import unittest
from copy import deepcopy

from report_document import blocks, messages, status
from workflow_export import quality_export


def snapshot(state='decided', rare=False):
    metric={'value':.5,'low':.3,'high':.7}
    result={'status':state,'threshold':.4 if state=='decided' else None,'density':metric,'count':{'value':50,'low':30,'high':70},
        'precision':metric if state=='decided' else None,'recall':None if rare else metric,'recall_status':'not_estimable_rare' if rare else 'estimated',
        'auprc':metric,'multiplier':None if rare else metric,'answers_per_bin':[],'unknown':2,'disagreement':True}
    validation={'id':7,'applicable':True,'stage':'complete','method':'stratified_quantile6_v1','status':state,
        'thresholds':{'birdnet':{'threshold':result['threshold'],'reason':'target_unreachable' if state=='undecided' else None}},
        'results':{'birdnet':result},'counts':{'total':60,'answered':60,'unknown':2,'bins':[]}}
    species={'id':'sp','scientific_name':'Example bird','common_names':{'ja':'例鳥','en':'Example bird'}}
    return {'kind':'detection','engine':'birdnet','model_sha256':'model','threshold':result['threshold'] or .1,'window_s':3,
        'species':species,'summaries':[{'species':species,'candidate_windows':4,'eligible_windows':100,'candidate_rate':.04,'candidate_days':2}],
        'validation':validation,'figure_id':'F001'}


def manifest(snap, locale='ja'):
    return {'locale':locale,'site':{'name':'Site','timezone':'Asia/Tokyo','latitude':35,'longitude':138},
        'start_date':'2024-01-01','end_date':'2024-01-02','scope':{'recording_ids':[1],'recorded_seconds':60,
        'daily':[{'seconds':60}],'schedule':[],'analysed_seconds':{'BirdNET':60}},'presence':[{'species_id':'sp','status':'confirmed',
        'evidence_count':1,'listened_without_confirming':None}],'selected_species':[snap['species']],
        'file_guide':[],'saved_at':'2024-01-01T00:00:00+00:00','package_id':'p','schema':'s'}


class ReportDocumentTest(unittest.TestCase):
    def test_new_statuses(self):
        self.assertEqual(status(snapshot()),'decided')
        undecided=snapshot('undecided');self.assertEqual(status(undecided),'undecided')
        undecided['validation']['stage']='sample';self.assertEqual(status(undecided),'in_progress')
        undecided['validation']['applicable']=False;self.assertEqual(status(undecided),'stale')

    def test_report_uses_90_percent_results_and_rare_message(self):
        snap=snapshot(rare=True)
        text='\n'.join(str(v) for block in blocks(manifest(snap),[snap],{'per_species':[]}) for v in block.get('text',[]) if isinstance(block.get('text'),list))
        rendered=str(blocks(manifest(snap),[snap],{'per_species':[]}))
        self.assertIn(messages('ja')['rare_recall'],rendered)
        self.assertIn(messages('ja')['disagreement'],rendered)
        self.assertNotIn('片側95%',rendered)
        self.assertNotIn('5,000.0%',rendered)

    def test_quality_csv_has_new_metrics_without_old_goal_fields(self):
        snap=snapshot();result=snap['validation']['results']['birdnet']
        row=quality_export(snap['validation'],result)
        self.assertEqual((row['density_value'],row['precision_value'],row['unknown'],row['disagreement']),(.5,.5,2,True))
        self.assertNotIn('goal_met',row);self.assertNotIn('precision_lower_95',row)

    def test_report_groups_species_and_moves_unconfirmed_figures_to_appendix(self):
        confirmed=snapshot();not_found=deepcopy(confirmed);unchecked=deepcopy(confirmed)
        for snap,sid,figure in ((confirmed,'confirmed','F001'),(not_found,'not-found','F002'),(unchecked,'unchecked','F003')):
            snap['species']={**snap['species'],'id':sid,'scientific_name':sid,'common_names':{'ja':sid,'en':sid}}
            snap['summaries'][0]['species']=snap['species'];snap['figure_id']=figure
        report=manifest(confirmed)
        report['selected_species']=[snap['species'] for snap in (confirmed,not_found,unchecked)]
        report['presence']=[
            {'species_id':'confirmed','status':'confirmed','counting_status':'decided','evidence_count':1,'listened_without_confirming':None},
            {'species_id':'not-found','status':'not_found','counting_status':'undecided','evidence_count':0,'listened_without_confirming':10},
            {'species_id':'unchecked','status':'unchecked','counting_status':'not_started','evidence_count':0,'listened_without_confirming':None},
        ]
        content=blocks(report,[confirmed,not_found,unchecked],{'per_species':[]})
        headings=[block['text'] for block in content if block['type']=='heading']
        self.assertLess(headings.index('人が聞いて確認した種'),headings.index('聞いたが確認できなかった種'))
        self.assertLess(headings.index('聞いたが確認できなかった種'),headings.index('AIの候補のみ（人は未確認）'))
        cards=[block['cards'] for block in content if block['type']=='cards']
        self.assertEqual([[card['id'] for card in group] for group in cards],[['F001'],['F002','F003']])
        rendered=str(content)
        self.assertIn('数え方が決まった（BirdNET）',rendered)
        self.assertIn('数え方を決められなかった（AIの誤りが多いか、答えがばらついた）',rendered)
        self.assertNotIn('基準',rendered)


if __name__=='__main__':unittest.main()
