"""Reference integrity checks must not depend on the recognizer's answers."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from livesheet.testing.pdf_reference_validation import (
    classify_references, verification_summary, xml_meter_issues, diagnostic_outcome,
)


def score(meter='3', duration='4', implicit=False, extra=''):
    return f'''<score-partwise><part id="P1"><measure number="1"
        implicit="{'yes' if implicit else 'no'}"><attributes><divisions>1</divisions>
        <time><beats>{meter}</beats><beat-type>4</beat-type></time></attributes>
        <note><rest/><duration>{duration}</duration></note>{extra}
        </measure></part></score-partwise>'''


class ReferenceValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, xml):
        path = self.root / name / 'source.musicxml'
        path.parent.mkdir(exist_ok=True)
        path.write_text(xml)
        return path

    def test_overfull_source_is_detected_without_recognition(self):
        path = self.write('original', score())
        self.assertEqual(xml_meter_issues(path), [dict(part=1, measure=1, beats='4', budget='3')])
        self.assertEqual(xml_meter_issues(self.write('corrected', score(meter='4'))), [])

    def test_chords_and_parallel_voices_are_not_added_together(self):
        extra = '''<note><chord/><pitch><step>C</step><octave>4</octave></pitch>
            <duration>3</duration></note><backup><duration>3</duration></backup>
            <note><rest/><duration>3</duration></note>'''
        self.assertEqual(xml_meter_issues(self.write('voices', score(duration='3', extra=extra))), [])

    def test_pickup_is_allowed_but_not_overfull_pickup(self):
        self.assertEqual(xml_meter_issues(self.write('pickup', score(duration='1', implicit=True))), [])
        self.assertTrue(xml_meter_issues(self.write('overfull', score(implicit=True))))
        self.assertTrue(xml_meter_issues(self.write('short', score(duration='1'))))

    def test_inherited_meter_and_explicit_change(self):
        original = score().replace('</part>', '''<measure number="2">
            <note><rest/><duration>4</duration></note></measure></part>''')
        self.assertEqual(len(xml_meter_issues(self.write('inherit', original))), 2)
        changed = original.replace('<measure number="2">', '''<measure number="2">
            <attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes>''')
        self.assertEqual(len(xml_meter_issues(self.write('change', changed))), 1)

    def test_unknown_and_midbar_meters_block_preflight(self):
        missing = score().replace('<time><beats>3</beats><beat-type>4</beat-type></time>', '')
        with self.assertRaisesRegex(ValueError, 'Missing reference meter'):
            xml_meter_issues(self.write('missing', missing))
        with self.assertRaisesRegex(ValueError, 'Mid-measure'):
            xml_meter_issues(self.write('midbar', score(extra='''<attributes><time>
                <beats>4</beats><beat-type>4</beat-type></time></attributes>''').replace(
                    '<time><beats>3</beats><beat-type>4</beat-type></time>', '')))

    def setup_policy(self):
        original = self.write('original', score())
        self.write('corrected', score(meter='4'))
        entries = [dict(id=name, family='test') for name in ('original', 'corrected')]
        policy = dict(original=dict(sourceSHA256=hashlib.sha256(original.read_bytes()).hexdigest(),
                                    issues=xml_meter_issues(original), replacement='corrected', expectedOutcome='overfull-rejection'))
        return entries, policy

    def policy(self, policy):
        (self.root / 'reference-diagnostics.json').write_text(json.dumps(policy))

    def test_unlisted_invalid_reference_blocks_run(self):
        entries, _ = self.setup_policy()
        with self.assertRaisesRegex(ValueError, 'Invalid reference original'):
            classify_references(entries, self.root)

    def test_known_invalid_reference_is_diagnostic_not_playback_success(self):
        entries, policy = self.setup_policy()
        self.policy(policy)
        checks = classify_references(entries, self.root)
        rows = [dict(reference=checks['original'], passed=None, diagnosticCheckPassed=True),
                dict(reference=checks['corrected'], passed=True)]
        summary = verification_summary(rows)
        self.assertEqual(summary, dict(passed=True, counts=dict(passed=1), diagnosticCount=1, diagnosticChecksPassed=True))
        rows[0]['diagnosticCheckPassed'] = False
        self.assertFalse(verification_summary(rows)['passed'])
        rows[0]['diagnosticCheckPassed'] = True
        rows[1]['passed'] = False
        self.assertFalse(verification_summary(rows)['passed'])
        self.assertFalse(verification_summary(rows[:1])['passed'])

    def test_stale_policy_or_invalid_replacement_blocks_run(self):
        entries, policy = self.setup_policy()
        for field, value in [('sourceSHA256', 'wrong'), ('issues', []), ('replacement', 'original')]:
            changed = deepcopy(policy)
            changed['original'][field] = value
            self.policy(changed)
            with self.assertRaises(ValueError):
                classify_references(entries, self.root)
        self.policy(policy)
        self.write('corrected', score())
        with self.assertRaisesRegex(ValueError, 'Invalid reference corrected'):
            classify_references(entries, self.root)

    def test_rejection_must_have_the_expected_reason_and_no_delivery(self):
        ref = dict(expectedOutcome='overfull-rejection')
        self.assertFalse(diagnostic_outcome(ref, False, self.root))
        (self.root / 'failure.json').write_text(json.dumps(dict(error='repair_validation_failed')))
        quality = self.root / 'quality.json'
        quality.write_text(json.dumps(dict(status='failed', reason='ValueError: repair-introduced-overfull-measure')))
        self.assertTrue(diagnostic_outcome(ref, False, self.root))
        self.assertFalse(diagnostic_outcome(ref, True, self.root))
        quality.write_text(json.dumps(dict(status='failed', reason='timeout')))
        self.assertFalse(diagnostic_outcome(ref, False, self.root))
        quality.write_text(json.dumps(dict(status='failed', reason='ValueError: repair-introduced-overfull-measure')))
        (self.root / 'completion.json').write_text('{}')
        self.assertFalse(diagnostic_outcome(ref, False, self.root))


if __name__ == '__main__':
    unittest.main()
