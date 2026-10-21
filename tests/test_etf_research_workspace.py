import contextlib
import base64
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest.mock import patch

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
import etf_research_workspace as research


def summary(day='2026-10-21',version='v1',reverse=False):
    rows=[{'section':'research','number':'15','title':'Trading filters','url':'https://example.org/filters?utm_source=rss'},
          {'section':'supplement','number':'1','title':'Liquidity','url':'https://example.org/liquidity'}]
    if reverse:rows.reverse()
    metadata,manifest=mail_fixture(day)
    return {'mail_id':research.mail_mapping(metadata,manifest)['mail_id'],'report_date':day,'run_id':'run-'+day,
            'version':version,'text':'Visible summary '+day+' '+version,'items':rows}


def mail_fixture(day='2026-10-21'):
    subject='Synthetic report - '+day
    body='# '+subject+'\n\n## research\n### 15. Trading filters\n- 链接：https://example.org/filters\n\n## supplement\n### 1. Liquidity\n- 原文链接：https://example.org/liquidity\n'
    manifest={'run_id':'run-'+day,'head_sha':'b'*40,'body_sha256':research.hashlib.sha256(body.encode()).hexdigest(),
              'selected_items':[{'title':'Liquidity','url':'https://example.org/liquidity'},
                                {'title':'Trading filters','url':'https://example.org/filters'}]}
    return {'subject':subject,'body':body},manifest


def request(intent='deep_read',id=None):
    return {'id':id or 'user-message-'+intent,'text':'Explicit synthetic user request for '+intent,'source':'user','intent':intent}


def evidence(url='https://example.org/paper',family='original-study',capture='a'*64):
    capture_text='Synthetic captured source, version '+capture
    return {'source_url':url,'source_version':'v1','capture_sha256':research.hashlib.sha256(capture_text.encode()).hexdigest(),
            'capture_text':capture_text,'scope':'abstract',
            'family':family,'stance':'support','note_zh':'Synthetic author-reported abstract; not independent replication.'}


def revision(tid,version=0,mode='interactive',items=None,event_count=1):
    return {'topic_id':tid,'expected_version':version,'expected_event_count':event_count,'execution_mode':mode,'judgment_zh':'Limited source-based judgment.',
            'change_zh':'First baseline.' if not version else 'New contradictory evidence; previous judgment weakened.',
            'open_questions':['Replication unavailable.'],'evidence':items or [evidence()],'retired_evidence':{}}


class ResearchWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)/'state'
        for day in ('2026-10-21','2026-10-22'):
            research.register_mail(self.root,*mail_fixture(day),'register-'+day)
    def tearDown(self):self.tmp.cleanup()
    def topic(self):
        s=research.import_summary(self.root,summary(),'import-1')
        t=research.create_topic(self.root,{'selection':{'summary_id':s['summary_id'],'display_number':1},
                                         'question':'Does prediction improve net returns?','request':request()},'topic-1')
        return s,t

    def renew(self,tid):
        return research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,
                                              'state':'once','request':request('once')},'renew-once')

    def test_visible_summary_and_original_mapping_are_one_immutable_snapshot(self):
        s=research.import_summary(self.root,summary(),'import-1')
        row=research.resolve(research.load_state(self.root),{'summary_id':s['summary_id'],'display_number':1})
        self.assertEqual(row['number'],'15');self.assertEqual(row['section'],'research')
        self.assertEqual(row['url'],'https://example.org/filters')
        self.assertEqual(research.load_state(self.root)['summaries'][s['summary_id']]['payload']['text'],summary()['text'])

    def test_reordering_new_version_keeps_article_identity_and_old_number_snapshot(self):
        a=research.import_summary(self.root,summary(),'a');b=research.import_summary(self.root,summary(version='v2',reverse=True),'b')
        self.assertEqual(a['items'][0]['item_id'],b['items'][1]['item_id'])
        self.assertNotEqual(a['summary_id'],b['summary_id'])
        state=research.load_state(self.root)
        self.assertEqual(research.resolve(state,{'summary_id':a['summary_id'],'display_number':1})['title'],'Trading filters')
        self.assertEqual(research.resolve(state,{'summary_id':b['summary_id'],'display_number':1})['title'],'Liquidity')

    def test_same_version_cannot_silently_replace_text_or_reorder(self):
        research.import_summary(self.root,summary(),'a');before=(self.root/'workspace.json').read_bytes()
        for key in ('text','reorder'):
            data=summary()
            if key=='text':data['text']='Changed conclusion'
            else:data['items'].reverse()
            with self.subTest(key=key),self.assertRaisesRegex(ValueError,'immutable'):
                research.import_summary(self.root,data,'attempt-'+key)
        self.assertEqual(before,(self.root/'workspace.json').read_bytes())

    def test_cross_day_bare_number_does_not_guess_latest(self):
        research.import_summary(self.root,summary(),'a');research.import_summary(self.root,summary('2026-10-22'),'b')
        with self.assertRaises(research.AmbiguousSelection):research.resolve(research.load_state(self.root),{'display_number':1})
        row=research.resolve(research.load_state(self.root),{'report_date':'2026-10-21','display_number':1})
        self.assertEqual(row['report_date'],'2026-10-21')

    def test_date_alone_does_not_choose_among_revised_summaries(self):
        research.import_summary(self.root,summary(),'a');research.import_summary(self.root,summary(version='v2',reverse=True),'b')
        with self.assertRaises(research.AmbiguousSelection):
            research.resolve(research.load_state(self.root),{'report_date':'2026-10-21','display_number':1})

    def test_duplicate_original_section_number_is_not_disambiguated_by_source_order(self):
        data=summary();data['items'][1].update(section='research',number='15')
        with self.assertRaisesRegex(ValueError,'section/number'):research.import_summary(self.root,data,'a')
        self.assertFalse(research.load_state(self.root)['summaries'])

    def test_invalid_source_date_or_missing_visible_text_is_refused(self):
        for bad in ('javascript:alert(1)','https://user:pass@example.org/private','https://example.org/a b'):
            data=summary();data['items'][0]['url']=bad
            with self.subTest(url=bad),self.assertRaises(ValueError):research.import_summary(self.root,data,'a')
        data=summary();data['report_date']='2026-02-30'
        with self.assertRaises(ValueError):research.import_summary(self.root,data,'a')
        data=summary();data['text']=''
        with self.assertRaises(ValueError):research.import_summary(self.root,data,'a')

    def test_topic_once_is_default_and_is_bound_to_exact_summary(self):
        s,t=self.topic();self.assertEqual(t['state'],'once');self.assertEqual(t['anchor']['summary_id'],s['summary_id'])
        self.assertEqual(t['anchor']['number'],'15');self.assertEqual(t['state_events'][0]['request'],request())

    def test_one_off_read_cannot_be_silently_scheduled(self):
        _,t=self.topic();before=(self.root/'workspace.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'tracking authorization'):
            research.record_revision(self.root,revision(t['topic_id'],mode='scheduled'),'r1')
        self.assertEqual(before,(self.root/'workspace.json').read_bytes())

    def test_tracking_needs_specific_user_intent_and_records_transition(self):
        _,t=self.topic();tid=t['topic_id']
        data={'topic_id':tid,'expected_state':'once','expected_event_count':1,'state':'tracked','request':request()}
        with self.assertRaisesRegex(ValueError,'explicit user intent'):research.set_status(self.root,data,'bad')
        data['request']=request('track');data['request']['source']='assistant'
        with self.assertRaisesRegex(ValueError,'explicit user intent'):research.set_status(self.root,data,'bad')
        data['request']=request('track')
        research.set_status(self.root,data,'track')
        result=research.record_revision(self.root,revision(tid,mode='scheduled',event_count=2),'r1')
        self.assertEqual(result['version'],1);self.assertEqual(research.load_state(self.root)['topics'][tid]['state'],'tracked')

    def test_wait_blocks_research_and_never_auto_upgrades(self):
        _,t=self.topic();tid=t['topic_id']
        research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,'state':'wait','request':request('wait')},'wait')
        with self.assertRaisesRegex(ValueError,'waiting/paused'):research.record_revision(self.root,revision(tid),'r')
        self.assertEqual(research.load_state(self.root)['topics'][tid]['state'],'wait')

    def test_stale_status_compare_and_set_does_not_overwrite_new_request(self):
        _,t=self.topic();tid=t['topic_id']
        research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,'state':'wait','request':request('wait')},'wait')
        with self.assertRaisesRegex(ValueError,'state changed'):
            research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,'state':'tracked','request':request('track')},'track')

    def test_revision_chain_retains_old_judgment_and_counterevidence(self):
        _,t=self.topic();tid=t['topic_id'];first=research.record_revision(self.root,revision(tid),'r1')
        opposing=evidence(url='https://example.org/counter',family='counter-study',capture='b'*64);opposing['stance']='oppose'
        self.renew(tid)
        second=research.record_revision(self.root,revision(tid,1,items=[evidence(),opposing],event_count=2),'r2')
        topic=research.load_state(self.root)['topics'][tid]
        self.assertEqual(topic['revisions'][0],first);self.assertEqual(second['parent_hash'],first['hash'])
        self.assertEqual(second['evidence_family_count'],2);self.assertEqual(topic['state'],'once')

    def test_same_underlying_study_at_two_urls_is_one_recorded_family(self):
        _,t=self.topic()
        r=research.record_revision(self.root,revision(t['topic_id'],items=[evidence(),evidence('https://example.org/reprint')]),'r1')
        self.assertEqual(len(r['evidence']),2);self.assertEqual(r['evidence_family_count'],1)

    def test_same_url_new_capture_keeps_distinct_evidence_versions(self):
        _,t=self.topic();r=research.record_revision(self.root,revision(t['topic_id'],items=[evidence(),evidence(capture='b'*64)]),'r1')
        self.assertNotEqual(r['evidence'][0]['evidence_id'],r['evidence'][1]['evidence_id'])
        self.assertEqual(r['evidence'][0]['capture_text'],evidence()['capture_text'])

    def test_evidence_capture_text_must_match_saved_hash(self):
        _,t=self.topic();bad=evidence();bad['capture_text']='Silently replaced source excerpt.'
        with self.assertRaisesRegex(ValueError,'retained evidence text'):
            research.record_revision(self.root,revision(t['topic_id'],items=[bad]),'bad-capture')
        self.assertEqual(research.pending(research.load_state(self.root))['once'],[t['topic_id']])

    def test_evidence_cannot_disappear_without_disposition(self):
        _,t=self.topic();tid=t['topic_id'];first=research.record_revision(self.root,revision(tid),'r1')
        self.renew(tid)
        new=revision(tid,1,items=[evidence('https://example.org/corrected',capture='c'*64)],event_count=2)
        with self.assertRaisesRegex(ValueError,'explicit disposition'):research.record_revision(self.root,new,'r2-bad')
        new['retired_evidence']={first['evidence'][0]['evidence_id']:'Author retracted original results; retained in version 1.'}
        second=research.record_revision(self.root,new,'r2')
        self.assertEqual(second['version'],2);self.assertEqual(research.load_state(self.root)['topics'][tid]['revisions'][0],first)

    def test_stale_expected_research_version_is_rejected(self):
        _,t=self.topic();research.record_revision(self.root,revision(t['topic_id']),'r1')
        with self.assertRaisesRegex(ValueError,'version changed'):research.record_revision(self.root,revision(t['topic_id']),'stale')

    def test_once_is_consumed_after_success_and_only_new_user_request_reopens_it(self):
        _,t=self.topic();tid=t['topic_id']
        self.assertEqual(research.pending(research.load_state(self.root)),{'once':[tid],'tracked':[]})
        research.record_revision(self.root,revision(tid),'r1')
        self.assertEqual(research.pending(research.load_state(self.root)),{'once':[],'tracked':[]})
        with self.assertRaisesRegex(ValueError,'already completed'):
            research.record_revision(self.root,revision(tid,1),'silent-second-day')
        repeated={'topic_id':tid,'expected_state':'once','expected_event_count':1,'state':'once',
                  'request':request('once',id=request()['id'])}
        with self.assertRaisesRegex(ValueError,'new user request ID'):research.set_status(self.root,repeated,'reused-request')
        self.renew(tid)
        self.assertEqual(research.pending(research.load_state(self.root))['once'],[tid])
        research.record_revision(self.root,revision(tid,1,event_count=2),'r2')
        self.assertEqual(research.pending(research.load_state(self.root))['once'],[])

    def test_failed_once_commit_stays_pending_for_same_operation_retry(self):
        _,t=self.topic();tid=t['topic_id']
        with patch.object(research.os,'replace',side_effect=OSError('precommit')):
            with self.assertRaises(OSError):research.record_revision(self.root,revision(tid),'r1')
        self.assertEqual(research.pending(research.load_state(self.root))['once'],[tid])
        research.record_revision(self.root,revision(tid),'r1')
        self.assertEqual(research.pending(research.load_state(self.root))['once'],[])

    def test_authorization_aba_and_stale_inflight_research_are_rejected(self):
        _,t=self.topic();tid=t['topic_id']
        research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,
                                      'state':'wait','request':request('wait')},'pause')
        research.set_status(self.root,{'topic_id':tid,'expected_state':'wait','expected_event_count':2,
                                      'state':'once','request':request('once')},'resume-once')
        with self.assertRaisesRegex(ValueError,'events changed'):
            research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,
                                          'state':'tracked','request':request('track')},'stale-track')
        with self.assertRaisesRegex(ValueError,'events changed'):
            research.record_revision(self.root,revision(tid),'stale-inflight')
        self.assertEqual(research.pending(research.load_state(self.root)),{'once':[tid],'tracked':[]})

    def test_pending_topic_queue_is_independent_of_repeated_mail_run(self):
        _,t=self.topic();tid=t['topic_id']
        research.set_status(self.root,{'topic_id':tid,'expected_state':'once','expected_event_count':1,
                                      'state':'tracked','request':request('track')},'track')
        research.record_revision(self.root,revision(tid,mode='scheduled',event_count=2),'scheduled-1')
        # No new mail and the imported run is identical; tracking still survives.
        research.import_summary(self.root,summary(),'import-1')
        self.assertEqual(research.pending(research.load_state(self.root)),{'once':[],'tracked':[tid]})
        research.record_revision(self.root,revision(tid,1,mode='scheduled',event_count=2),'scheduled-2')
        self.assertEqual(len(research.load_state(self.root)['topics'][tid]['revisions']),2)

    def test_identical_retry_returns_receipt_without_writing_or_changing_time(self):
        _,t=self.topic();data=revision(t['topic_id']);first=research.record_revision(self.root,data,'r1')
        before=(self.root/'workspace.json').read_bytes()
        second=research.record_revision(self.root,data,'r1')
        self.assertEqual(first,second);self.assertEqual(before,(self.root/'workspace.json').read_bytes())

    def test_operation_id_cannot_be_reused_for_different_payload(self):
        research.import_summary(self.root,summary(),'a')
        with self.assertRaisesRegex(ValueError,'idempotency key'):research.import_summary(self.root,summary('2026-10-22'),'a')

    def test_failed_replace_keeps_prior_mapping_and_operation_receipts_together(self):
        research.import_summary(self.root,summary(),'a');before=(self.root/'workspace.json').read_bytes()
        with patch.object(research.os,'replace',side_effect=OSError('injected precommit crash')):
            with self.assertRaises(OSError):research.import_summary(self.root,summary('2026-10-22'),'b')
        self.assertEqual(before,(self.root/'workspace.json').read_bytes())
        self.assertEqual(list(self.root.glob('.workspace-*.tmp')),[])
        self.assertNotIn('b',research.load_state(self.root)['operations'])

    def test_unknown_success_after_replace_retries_same_operation_exactly_once(self):
        real_replace=research.os.replace
        def committed_but_response_lost(src,dst):
            real_replace(src,dst);raise OSError('injected postcommit response loss')
        with patch.object(research.os,'replace',side_effect=committed_but_response_lost):
            with self.assertRaises(OSError):research.import_summary(self.root,summary(),'a')
        before=(self.root/'workspace.json').read_bytes()
        result=research.import_summary(self.root,summary(),'a')
        self.assertEqual(len(research.load_state(self.root)['summaries']),1)
        self.assertEqual(result['payload']['report_date'],'2026-10-21');self.assertEqual(before,(self.root/'workspace.json').read_bytes())

    def test_writer_contention_fails_closed(self):
        with research.locked(self.root):
            with self.assertRaises(research.WorkspaceBusy):research.import_summary(self.root,summary(),'a')
        research.import_summary(self.root,summary(),'a')

    def test_process_crash_releases_os_lock_without_manual_unlock(self):
        code='import sys,os;sys.path.insert(0,sys.argv[1]);import etf_research_workspace as r\nwith r.locked(sys.argv[2]):os._exit(0)'
        done=subprocess.run([sys.executable,'-c',code,str(SCRIPTS),str(self.root)],capture_output=True,timeout=10)
        self.assertEqual(done.returncode,0,done.stderr)
        research.import_summary(self.root,summary(),'a')

    def test_orphan_temp_is_not_mistaken_for_a_committed_workspace(self):
        (self.root/'.workspace-orphan.tmp').write_text('{broken',encoding='utf-8')
        self.assertEqual(research.load_state(self.root)['revision'],2)
        research.import_summary(self.root,summary(),'a')
        self.assertTrue((self.root/'.workspace-orphan.tmp').exists())

    def test_tampered_mapping_or_revision_is_not_silently_accepted(self):
        _,t=self.topic();research.record_revision(self.root,revision(t['topic_id']),'r1')
        original=json.loads((self.root/'workspace.json').read_text(encoding='utf-8'))
        for which in ('mapping','judgment','anchor'):
            state=copy.deepcopy(original)
            if which=='mapping':next(iter(state['summaries'].values()))['items'][0]['url']='https://wrong.example'
            elif which=='judgment':state['topics'][t['topic_id']]['revisions'][0]['judgment_zh']='Changed silently'
            else:state['topics'][t['topic_id']]['anchor']['number']='2'
            (self.root/'workspace.json').write_text(json.dumps(state),encoding='utf-8')
            with self.subTest(which=which),self.assertRaises(ValueError):research.load_state(self.root)

    def test_cli_import_resolve_show_and_mutation_without_id_is_refused(self):
        inp=Path(self.tmp.name)/'input.json';inp.write_text(json.dumps(summary()),encoding='utf-8')
        args=['--workspace',str(self.root),'--input',str(inp)]
        with self.assertRaisesRegex(ValueError,'operation-id'):research.main(['import-summary',*args])
        with contextlib.redirect_stdout(io.StringIO()) as capture:
            self.assertEqual(research.main(['import-summary',*args,'--operation-id','a']),0)
        sid=json.loads(capture.getvalue())['summary_id']
        inp.write_text(json.dumps({'summary_id':sid,'display_number':1}),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as capture:research.main(['resolve',*args])
        self.assertEqual(json.loads(capture.getvalue())['number'],'15')
        with contextlib.redirect_stdout(io.StringIO()) as capture:research.main(['show','--workspace',str(self.root)])
        self.assertEqual(json.loads(capture.getvalue())['revision'],3)

    def test_source_body_numbers_override_backend_array_order(self):
        metadata,manifest=mail_fixture();mapping=research.mail_mapping(metadata,manifest)
        self.assertEqual([v['number'] for v in mapping['items']],['15','1'])
        manifest['selected_items'].reverse()
        self.assertEqual(mapping,research.mail_mapping(metadata,manifest))
        data=summary();data['items'][0]['number']='1'
        with self.assertRaisesRegex(ValueError,'authoritative body'):research.import_summary(self.root,data,'wrong-array-position')

    def test_real_fresh_lobgrad_quant1_filters_quant15_and_stable_global_ids(self):
        fixture=Path(__file__).parent/'fixtures'/'etf_archived'/'fresh.zip.b64'
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(fixture.read_bytes()))) as archived:
            metadata=json.loads(archived.read('metadata.json'));manifest=json.loads(archived.read('collection_manifest.json'))
        mapping=research.register_mail(self.root,metadata,manifest,'fresh-mail')
        self.assertEqual(len(mapping['items']),32)
        selected={v['url']:v for v in mapping['items']}
        lob=selected['https://arxiv.org/abs/2610.25495'];filters=selected['https://arxiv.org/abs/2610.24558']
        self.assertEqual((lob['section'],lob['number'],lob['global_id']),('量化策略影响','1','E20261021-39518483125-003'))
        self.assertEqual((filters['section'],filters['number'],filters['global_id']),('量化策略影响','15','E20261021-39518483125-017'))
        payload={'mail_id':mapping['mail_id'],'report_date':mapping['report_date'],'run_id':mapping['run_id'],
                 'version':'v1','text':'Synthetic preview of actual archived items.',
                 'items':[{k:v[k] for k in ('section','number','title','url')} for v in (lob,filters)]}
        a=research.import_summary(self.root,payload,'fresh-summary1')
        payload['items'].reverse();payload['version']='v2'
        b=research.import_summary(self.root,payload,'fresh-summary2')
        for key in ('item_id','global_id','number','section','url'):
            self.assertEqual(a['items'][0][key],b['items'][1][key]);self.assertEqual(a['items'][1][key],b['items'][0][key])
        state=research.load_state(self.root)
        self.assertEqual(research.resolve(state,{'summary_id':b['summary_id'],'global_id':filters['global_id']})['display_number'],1)
        payload['version']='wrong';payload['items'][0]['number']='17'
        with self.assertRaisesRegex(ValueError,'authoritative body'):research.import_summary(self.root,payload,'fresh-array17-rejected')

    def test_missing_ambiguous_or_changed_authoritative_blocks_fail_closed(self):
        metadata,manifest=mail_fixture()
        with self.assertRaisesRegex(ValueError,'hash'):research.mail_mapping({**metadata,'body':metadata['body']+'changed'},manifest)
        for transform in (lambda s:s.replace('### 15.','### unnumbered.'),
                          lambda s:s.replace('## supplement','## research').replace('### 1.','### 15.'),
                          lambda s:s+'\n## appendix\n### 2. copy\n- 链接：https://example.org/filters\n'):
            changed={**metadata,'body':transform(metadata['body'])}
            changed_manifest={**manifest,'body_sha256':research.hashlib.sha256(changed['body'].encode()).hexdigest()}
            with self.subTest(body=changed['body']),self.assertRaises(ValueError):research.mail_mapping(changed,changed_manifest)
        changed={**metadata,'body':metadata['body']+'\nVersion changed.'}
        changed_manifest={**manifest,'body_sha256':research.hashlib.sha256(changed['body'].encode()).hexdigest()}
        with self.assertRaisesRegex(ValueError,'immutable'):research.register_mail(self.root,changed,changed_manifest,'fork')

    def test_cli_map_and_register_authoritative_mail(self):
        source=Path(self.tmp.name)/'source';source.mkdir()
        metadata,manifest=mail_fixture()
        (source/'metadata.json').write_text(json.dumps(metadata),encoding='utf-8')
        (source/'collection_manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
        with contextlib.redirect_stdout(io.StringIO()) as output:research.main(['map-mail','--source-dir',str(source)])
        mapping=json.loads(output.getvalue());self.assertEqual(mapping,research.mail_mapping(metadata,manifest))
        with contextlib.redirect_stdout(io.StringIO()):
            research.main(['register-mail','--source-dir',str(source),'--workspace',str(self.root),'--operation-id','register-cli'])
        self.assertIn(mapping['mail_id'],research.load_state(self.root)['mails'])


if __name__=='__main__':unittest.main()
