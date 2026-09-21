import importlib.util
from pathlib import Path
import unittest


SPEC = importlib.util.spec_from_file_location(
    'windows_resume_acceptance',
    Path(__file__).resolve().parents[2] / 'platforms' / 'windows' / 'scripts' / 'windows-resume-acceptance.py',
)
ACCEPTANCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACCEPTANCE)


class ResumeCompletionEvidenceTests(unittest.TestCase):
    def turn(self, text, phase='final_answer', status='completed'):
        return {'status': status, 'items': [
            {'type': 'agentMessage', 'phase': phase, 'text': text},
        ]}

    def test_started_is_not_completion(self):
        evidence = ACCEPTANCE.completion_evidence(self.turn('DONE', status='inProgress'))
        self.assertFalse(evidence['verified'])

    def test_empty_final_is_failure_even_when_completed(self):
        for text in ('', ' \n ', None):
            with self.subTest(text=text):
                evidence = ACCEPTANCE.completion_evidence(self.turn(text))
                self.assertEqual(evidence['reason'], 'empty_final_reply')

    def test_commentary_does_not_replace_final(self):
        evidence = ACCEPTANCE.completion_evidence(self.turn('Working', phase='commentary'))
        self.assertFalse(evidence['verified'])

    def test_expected_marker_must_match(self):
        self.assertFalse(ACCEPTANCE.completion_evidence(self.turn('Other'), 'DONE')['verified'])
        evidence = ACCEPTANCE.completion_evidence(self.turn('DONE'), 'DONE')
        self.assertTrue(evidence['verified'])
        self.assertTrue(evidence['expectedTextMatched'])

    def test_nonempty_final_without_marker(self):
        evidence = ACCEPTANCE.completion_evidence(self.turn('Finished'))
        self.assertTrue(evidence['verified'])
        self.assertFalse(evidence['expectedTextMatched'])


if __name__ == '__main__':
    unittest.main()
