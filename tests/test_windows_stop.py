import copy
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from windows_native import NativeTextController, WindowsNativeHandler
from windows_stop import NativeStopController, validate_stop_payload
import windows_desktop_helper as helper
import test_mac_bridge as fixtures

THREAD = '11111111-1111-4111-8111-111111111111'  # Synthetic fixture.


class StopTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.data = {'id': THREAD, 'name': 'test', 'turns': [{'id': 'turn-a', 'status': 'inProgress'}]}
        self.client = Mock()
        self.client.read_thread.side_effect = lambda _: {'thread': copy.deepcopy(self.data)}
        self.client.request.return_value = {'data': [{'id': THREAD, 'name': 'test'}], 'nextCursor': None}
        self.native = NativeTextController(self.client, Path(tmp.name), 'key')
        self.dispatch = Mock(side_effect=self.action)
        self.stop = NativeStopController(self.native, dispatch=self.dispatch, attempts=1)

    def guard(self):
        return {'issuedAt': int(time.time()*1000), 'documentId': [1,2], 'buttonId': [3,4]}

    def action(self, phase, payload):
        if phase == 'prepare':
            return {'status': 'prepared', 'guard': self.guard()}
        self.data['turns'][-1]['status'] = 'interrupted'
        return {'status': 'invoke_requested'}

    def test_two_phase_stop_and_no_duplicate_click(self):
        self.assertTrue(self.stop.stop(THREAD,'turn-a','test')['interrupted'])
        self.assertTrue(self.stop.stop(THREAD,'turn-a','test')['alreadyFinished'])
        self.assertEqual([c.args[0] for c in self.dispatch.call_args_list], ['prepare','commit'])
        self.client.interrupt_turn.assert_not_called()
        self.client.start_turn.assert_not_called()

    def test_navigation_completed_or_changed_turn_prevents_commit(self):
        for status, identifier in [('completed','turn-a'),('inProgress','turn-b')]:
            with self.subTest(status=status):
                self.setUp()
                def prepared(phase,payload):
                    self.data['turns'][-1].update(status=status,id=identifier)
                    return {'status':'prepared','guard':self.guard()}
                self.dispatch.side_effect=prepared
                self.assertFalse(self.stop.stop(THREAD,'turn-a','test')['ok'])
                self.assertEqual(self.dispatch.call_count,1)

    def test_old_turn_title_duplicate_or_inactive_refused(self):
        self.assertFalse(self.stop.stop(THREAD,'wrong','test')['ok'])
        self.assertFalse(self.stop.stop(THREAD,'turn-a','wrong')['ok'])
        self.client.request.return_value={'data':[], 'nextCursor':None}
        self.assertFalse(self.stop.stop(THREAD,'turn-a','test')['ok'])
        self.dispatch.assert_not_called()

    def test_uncertain_never_replays_after_restart(self):
        def uncertain(phase,payload):
            if phase == 'prepare': return {'status':'prepared','guard':self.guard()}
            raise TimeoutError()
        self.dispatch.side_effect=uncertain
        self.assertEqual(self.stop.stop(THREAD,'turn-a','test')['error'],'native_stop_uncertain')
        again=NativeStopController(self.native,dispatch=self.dispatch,attempts=1)
        self.assertEqual(again.stop(THREAD,'turn-a','test')['error'],'native_stop_uncertain')
        self.assertEqual(self.dispatch.call_count,2)
        self.data['turns'][-1]['status']='interrupted'
        self.assertTrue(again.stop(THREAD,'turn-a','test')['alreadyFinished'])
        self.assertEqual(self.dispatch.call_count,2)

    def test_stale_or_malformed_guard_refuses(self):
        payload={'threadId':THREAD,'expectedTitle':'test','guard':self.guard()}
        validate_stop_payload(payload,commit=True)
        for g in ({**self.guard(),'issuedAt':0},{**self.guard(),'buttonId':[True]}, {'issuedAt':0}):
            with self.assertRaises(ValueError):validate_stop_payload({**payload,'guard':g},commit=True)

    def test_reader_interrupted_is_not_proof_of_stop(self):
        self.data['path']='fixture.jsonl'
        self.data['turns'][-1]['status']='interrupted'
        self.dispatch.side_effect=lambda phase,payload: {'status':'prepared','guard':self.guard()} if phase=='prepare' else {'status':'invoke_requested'}
        with patch('windows_stop._rollout_activity_snapshot',return_value={'activeTurnId':'turn-a','status':'inProgress'}):
            self.assertEqual(self.stop.stop(THREAD,'turn-a','test')['error'],'native_stop_uncertain')
            self.assertEqual(self.dispatch.call_count,2)

    def test_helper_payload_and_fixed_script(self):
        delivery={'threadId':THREAD,'expectedTitle':'test','guard':None}
        request={'id':'a'*32,'instance':'test','action':'native-stop-prepare','createdAt':100,'delivery':delivery}
        helper.validate_request(request,'test',101)
        with self.assertRaises(ValueError):helper.validate_request({**request,'command':'bad'},'test',101)
        with patch.object(helper.subprocess,'run') as run:
            run.return_value=Mock(returncode=0,stdout='{"status":"prepared","targetThreadId":"'+THREAD+'"}')
            self.assertEqual(helper.run_native_stop(delivery,'prepare')['nativeStop']['status'],'prepared')
            self.assertEqual(run.call_args.args[0][-2:],['-StopPhase','prepare'])
            self.assertNotIn('test',run.call_args.args[0])


class StopApiTest(unittest.TestCase):
    request=fixtures.BridgeApiTest.request
    tearDown=fixtures.BridgeApiTest.tearDown

    def setUp(self):
        fixtures.BridgeApiTest.setUp(self)
        self.server.RequestHandlerClass=WindowsNativeHandler
        self.server.native_stop_controller=Mock()
        self.server.native_stop_controller.available.return_value=True
        self.server.native_stop_controller.stop.return_value={'ok':True,'interrupted':True}

    def test_auth_confirm_and_turn_required(self):
        payload={'confirm':True,'threadId':THREAD,'expectedTurnId':'turn-a','expectedTaskTitle':'test'}
        self.assertEqual(self.request('POST','/api/desktop/interrupt',payload,authorized=False)[0],401)
        self.assertEqual(self.request('POST','/api/desktop/interrupt',{**payload,'confirm':False})[0],400)
        self.server.native_stop_controller.stop.assert_not_called()
        self.assertEqual(self.request('POST','/api/desktop/interrupt',payload)[0],200)
        self.server.native_stop_controller.stop.assert_called_once_with(THREAD,'turn-a','test')

    def test_capability_is_explicit_and_fresh(self):
        self.assertTrue(self.request('GET','/api/desktop/interrupt/status')[1]['capabilities']['nativeStop'])
        self.server.native_stop_controller.available.return_value=False
        self.assertFalse(self.request('GET','/api/desktop/interrupt/status')[1]['capabilities']['nativeStop'])
        self.assertEqual(self.request('GET','/api/desktop/interrupt/status',authorized=False)[0],401)
