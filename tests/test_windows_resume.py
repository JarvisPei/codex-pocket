import copy
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from windows_native import NativeTextController, WindowsNativeHandler
from windows_resume import NativeResumeController
import windows_desktop_helper as helper
import test_mac_bridge as fixtures

THREAD = '33333333-3333-4333-8333-333333333333'  # Synthetic fixture.


class ResumeTest(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.data={'id':THREAD,'name':'test','turns':[{'id':'old','status':'interrupted'}]}
        self.client=Mock()
        self.client.read_thread.side_effect=lambda _: {'thread':copy.deepcopy(self.data)}
        self.client.request.return_value={'data':[{'id':THREAD,'name':'test'}],'nextCursor':None}
        self.native=NativeTextController(self.client,Path(tmp.name),'key')
        self.dispatch=Mock(side_effect=self.action)
        self.resume=NativeResumeController(self.native,dispatch=self.dispatch,attempts=1)
        self.rid=str(uuid.uuid4())

    def guard(self):
        return {'issuedAt':int(time.time()*1000),'buttonId':[1,2],'documentId':[3,4]}

    def action(self,phase,payload):
        if phase=='prepare':return {'status':'prepared','guard':self.guard()}
        self.data['turns'].append({'id':'new','status':'inProgress'})
        return {'status':'invoke_requested'}

    def test_resume_and_replay_after_restart(self):
        self.assertTrue(self.resume.resume(self.rid,THREAD,'old')['ok'])
        again=NativeResumeController(self.native,dispatch=self.dispatch,attempts=1)
        self.assertTrue(again.resume(self.rid,THREAD,'old')['desktop']['duplicateRequest'])
        self.assertEqual([c.args[0] for c in self.dispatch.call_args_list],['prepare','commit'])
        self.client.start_turn.assert_not_called();self.client.continue_turn.assert_not_called()

    def test_active_completed_or_stale_turn_refuses_without_ui(self):
        for status,turn in [('inProgress','old'),('completed','old'),('interrupted','wrong')]:
            self.data['turns'][-1]['status']=status
            self.assertFalse(self.resume.resume(str(uuid.uuid4()),THREAD,turn)['ok'])
        self.dispatch.assert_not_called()

    def test_navigation_race_refuses_commit(self):
        def prepare(*_):
            self.data['turns'].append({'id':'other','status':'inProgress'})
            return {'status':'prepared','guard':self.guard()}
        self.dispatch.side_effect=prepare
        self.assertFalse(self.resume.resume(self.rid,THREAD,'old')['ok'])
        self.assertEqual(self.dispatch.call_count,1)

    def test_unknown_ui_or_draft_refuses(self):
        self.dispatch.return_value={'status':'refused'};self.dispatch.side_effect=None
        self.assertTrue(self.resume.resume(self.rid,THREAD,'old')['retryAllowed'])
        self.assertEqual(self.dispatch.call_count,1)

    def test_uncertain_never_replays_but_late_receipt_reconciles(self):
        self.dispatch.side_effect=lambda phase,payload: {'status':'prepared','guard':self.guard()} if phase=='prepare' else {'status':'uncertain'}
        self.assertFalse(self.resume.resume(self.rid,THREAD,'old')['retryAllowed'])
        self.assertFalse(self.resume.resume(self.rid,THREAD,'old')['ok'])
        self.assertEqual(self.dispatch.call_count,2)
        with self.assertRaises(ValueError):self.resume.resume(str(uuid.uuid4()),THREAD,'old')
        self.data['turns'].append({'id':'new','status':'completed'})
        self.assertTrue(self.resume.resume(self.rid,THREAD,'old')['ok'])
        self.assertEqual(self.dispatch.call_count,2)

    def test_same_turn_can_become_active_and_invalid_guard_cannot_click(self):
        def action(phase,payload):
            if phase=='prepare':return {'status':'prepared','guard':self.guard()}
            self.data['turns'][-1]['status']='inProgress';return {'status':'invoke_requested'}
        self.dispatch.side_effect=action
        self.assertTrue(self.resume.resume(self.rid,THREAD,'old')['ok'])
        self.data['turns'][-1]['status']='interrupted'
        self.dispatch.reset_mock();self.dispatch.side_effect=lambda *_:{'status':'prepared','guard':{}}
        self.assertFalse(self.resume.resume(str(uuid.uuid4()),THREAD,'old')['ok'])
        self.assertEqual(self.dispatch.call_count,1)

    def test_reader_false_interrupted_does_not_resume_active_desktop(self):
        self.data['path']='fixture.jsonl'
        with patch('windows_stop._rollout_activity_snapshot',return_value={'activeTurnId':'old','status':'inProgress'}):
            self.assertFalse(self.resume.resume(self.rid,THREAD,'old')['ok'])
        self.dispatch.assert_not_called()

    def test_fixed_helper_action_and_resume_flag(self):
        p={'threadId':THREAD,'expectedTitle':'test','guard':None}
        helper.validate_request({'id':'a'*32,'instance':'x','createdAt':100,'action':'native-resume-prepare','delivery':p},'x',101)
        with patch.object(helper.subprocess,'run') as run:
            run.return_value=Mock(returncode=0,stdout='{"status":"prepared","targetThreadId":"'+THREAD+'"}')
            self.assertEqual(helper.run_native_stop(p,'prepare',resume=True)['nativeResume']['status'],'prepared')
            self.assertEqual(run.call_args.args[0][-2:],['-ResumePhase','prepare'])


class ResumeApiTest(unittest.TestCase):
    request=fixtures.BridgeApiTest.request
    tearDown=fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.RequestHandlerClass=WindowsNativeHandler
        self.server.native_resume_controller=Mock()
        self.server.native_resume_controller.resume.return_value={'ok':True,'mode':'desktop','desktop':{'ok':True}}

    def test_auth_and_empty_composer(self):
        url='/api/codex/threads/'+THREAD+'/continue'
        p={'requestId':str(uuid.uuid4()),'expectedTurnId':'old'}
        self.assertEqual(self.request('POST',url,p,authorized=False)[0],401)
        self.assertEqual(self.request('POST',url,{**p,'message':'draft'})[0],400)
        self.assertEqual(self.request('POST',url,{**p,'attachmentIds':['file']})[0],400)
        self.server.native_resume_controller.resume.assert_not_called()
        self.assertEqual(self.request('POST',url,p)[0],202)
        self.server.native_resume_controller.resume.assert_called_once_with(p['requestId'],THREAD,'old')

if __name__=='__main__':unittest.main()
