import json
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from livesheet.audit_pdf_recognition import meter_budgets, profile, signature


class RecognitionAuditTests(unittest.TestCase):
    def test_meter_inherits_and_handles_additive_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'score.mxl'
            with ZipFile(path, 'w') as archive:
                archive.writestr('META-INF/container.xml', '<container><rootfile full-path="score.xml"/></container>')
                archive.writestr('score.xml', '''<score-partwise><part>
                <measure><attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes></measure>
                <measure/><measure><attributes><time><beats>3+2</beats><beat-type>8</beat-type></time></attributes></measure>
                </part></score-partwise>''')
            self.assertEqual(meter_budgets([path]), [4,4,2.5])

    def test_excerpt_detects_timing_errors_even_with_perfect_note_mapping(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); score = root/'score.mxl'
            with ZipFile(score, 'w') as archive:
                archive.writestr('META-INF/container.xml', '<container><rootfile full-path="score.xml"/></container>')
                archive.writestr('score.xml', '<score-partwise><part><measure><attributes><time><beats>4</beats><beat-type>4</beat-type></time></attributes></measure></part></score-partwise>')
            note = dict(measureIndex=0, staff=1, midi=60, onset=2.5, duration=2, ties=[])
            path = root/'mapping.json'
            path.write_text(json.dumps(dict(musicxmlFiles=[str(score)],notes=[note],
                measures=[dict(number='1',page=1,duration=4.5)],
                summary=dict(omrHeads=1,unusedOmrHeads=0,unmatchedXmlNotes=0))))
            result,_ = profile(path, dict(measures=[dict(index=0,notes=[{**note,'onset':2}])]))
            self.assertEqual(result['unmappedNotes'], 0)
            self.assertEqual(len(result['overfullMeasures']), 1)
            self.assertFalse(result['checkedExcerpt'][0]['exact'])
            self.assertEqual(result['checkedExcerpt'][0]['missingOrChanged'], 1)

    def test_ties_and_duration_are_part_of_match_but_voice_labels_are_not(self):
        note = dict(staff=1,midi=60,onset=0,duration=1,ties=['start'],voice='1')
        self.assertEqual(signature(note), signature({**note,'voice':'3'}))
        self.assertNotEqual(signature(note), signature({**note,'ties':[]}))
        self.assertNotEqual(signature(note), signature({**note,'duration':.5}))

if __name__ == '__main__': unittest.main()
