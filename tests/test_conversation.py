import copy
import unittest

from quota_monitor.browser import ingest, summary
from quota_monitor.classify import classify
from quota_monitor.config import Config, ConfigError
from quota_monitor.service import parse_time
from quota_monitor.store import Store


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.config = Config({'accounts':[{'username':'thsottiaux','id':'123','verified':True,'kind':'personal'}],
                              'browser':{},'notifications':{'channel':'console'}})
        self.store = Store(':memory:')
        self.now = parse_time('2026-09-20T12:05:00Z').timestamp()
        self.parent = {'id':'2101093319501664368','username':'udiWertheimer',
                       'text':'ok tibo you owe us a banked reset. sorry I do not make the rules',
                       'created_at':'2026-09-19T07:37:00Z', 'complete':True,
                       'relation':'preceding_in_thread','evidence':'detail_order'}
        self.reply = {'id':'2101352781219258527','username':'thsottiaux',
                      'text':'OK fine. But it’s also still coming in Tuesday',
                      'created_at':'2026-09-20T12:00:00Z','complete':True,'context':[self.parent]}

    def tearDown(self): self.store.close()

    def test_contextual_reply_is_uncertain_with_separate_evidence_and_no_countdown(self):
        result=ingest({'posts':[self.reply]},self.config,self.store,self.now)
        self.assertEqual(len(result['alerts']),1)
        a=result['alerts'][0]
        self.assertEqual(a['kind'],'uncertain')
        self.assertIn('上下文',a['reason'])
        body=self.store.due(self.now)[0]['body']
        self.assertIn(self.reply['text'],body)
        self.assertIn('@udiWertheimer',body)
        self.assertIn('https://x.com/udiWertheimer/status/2101093319501664368',body)
        self.assertIn('未核实',body)
        self.assertIn('不生成倒计时',body)
        self.assertIn(self.parent['text'],a['text'])
        self.assertEqual(len(self.store.due(self.now+1000)),1)
        self.assertEqual(summary({'posts':[self.reply]})['posts'][0]['kind'],'uncertain')

    def test_original_rule_and_unrelated_context_do_not_promote(self):
        self.assertEqual(classify(self.reply['text']).kind,'ignore')
        for parent in ['Please reset my password.', 'Nice release.']:
            self.assertEqual(classify(self.reply['text'],context=parent).kind,'ignore')
        self.assertEqual(classify('Here is a photo of my dog.',context=self.parent['text']).kind,'ignore')
        self.assertEqual(classify('请重置密码。',context=self.parent['text']).kind,'ignore')
        d=classify('好吧，行。但它周二还是会到的。', context='你们欠我们一次额度重置。')
        self.assertEqual(d.kind,'uncertain');self.assertIsNone(d.reset_at)
        self.assertEqual(classify('No, not happening.',context=self.parent['text']).kind,'uncertain')

    def test_ignored_reply_can_promote_once_when_context_arrives(self):
        bare=copy.deepcopy(self.reply);bare.pop('context')
        self.assertEqual(ingest({'posts':[bare]},self.config,self.store,self.now)['alerts'],[])
        self.assertEqual(len(ingest({'posts':[self.reply]},self.config,self.store,self.now)['alerts']),1)
        changed=copy.deepcopy(self.reply);changed['context'][0]['text']+=' More discussion.'
        self.assertEqual(ingest({'posts':[changed]},self.config,self.store,self.now)['alerts'],[])
        self.assertEqual(self.store.status()['posts'],1)

    def test_invalid_context_and_unmonitored_outer_author_are_rejected(self):
        for changes in [{'complete':False},{'relation':'quoted'}, {'evidence':'unknown'},
                        {'id':self.reply['id']},{'id':'9999999999999999999'},
                        {'username':'../spoof'}, {'text':''}]:
            p=copy.deepcopy(self.reply);p['context'][0].update(changes)
            with self.subTest(changes=changes):
                self.assertEqual(ingest({'posts':[p]},self.config,self.store,self.now)['alerts'],[])
        p=copy.deepcopy(self.reply);p['username']='other'
        self.assertEqual(ingest({'posts':[p]},self.config,self.store,self.now)['alerts'],[])

    def test_context_disabled_and_expired_reply_never_alert(self):
        self.config.data['browser']['context_enabled']=False
        self.assertEqual(ingest({'posts':[self.reply]},self.config,self.store,self.now)['alerts'],[])
        self.config.data['browser']['context_enabled']=True
        self.assertEqual(ingest({'posts':[self.reply]},self.config,self.store,self.now+86400)['alerts'],[])

    def test_browser_context_options_validate(self):
        for settings in [{'reset_feed_enabled':'true'},{'reset_feed_user_agent':'bad\nagent'},{'context_enabled':'true'},{'conversation_urls':'https://x.com/demo/status/1'},
                         {'conversation_urls':['file:///C:/secret']},
                         {'conversation_urls':['https://example.com/demo/status/1']},
                         {'conversation_urls':['https://x.com/demo/status/1']*6}]:
            self.config.data['browser']=settings
            with self.subTest(settings=settings),self.assertRaises(ConfigError):self.config.validate_browser()

    def test_feed_parent_attribution_and_no_cross_source_replay(self):
        p=copy.deepcopy(self.reply)
        p.update(discovery_source='https://codex-reset.com/',parent_id=self.parent['id'],parent_username='udiWertheimer')
        p['context'][0].update(relation='parent_reported_by_feed',evidence='codex_reset_parent_link',reply_id=p['id'])
        a=ingest({'posts':[p]},self.config,self.store,self.now)['alerts']
        self.assertEqual(len(a),1)
        self.assertIn('Data: codex-reset.com',a[0]['text'])
        self.assertIn('Data: codex-reset.com',self.store.due(self.now)[0]['body'])
        self.assertEqual(ingest({'posts':[self.reply]},self.config,self.store,self.now)['alerts'],[])
        self.assertEqual(self.store.status()['posts'],1)

    def test_forged_feed_parent_cannot_promote(self):
        for changes in [{'reply_id':'1'}, {'username':'someone_else'}, {'id':'1'}]:
            p=copy.deepcopy(self.reply)
            p.update(discovery_source='https://codex-reset.com/',parent_id=self.parent['id'],parent_username='udiWertheimer')
            p['context'][0].update(relation='parent_reported_by_feed',evidence='codex_reset_parent_link',reply_id=p['id'])
            p['context'][0].update(changes)
            self.assertEqual(ingest({'posts':[p]},self.config,self.store,self.now)['alerts'],[])


if __name__=='__main__':unittest.main()
