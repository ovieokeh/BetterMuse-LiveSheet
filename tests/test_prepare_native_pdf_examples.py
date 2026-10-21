"""Contract checks for notation enrichment; no recognition or network needed."""
import base64
import json
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET
from livesheet.companion import notation_analysis, staff_regions, arpeggiated_chords, rolled_note_source


class NotationAnalysisTests(unittest.TestCase):
    def test_uploaded_web_revision_is_full_digest_before_native_enrichment(self):
        from livesheet.worker import finalize_web_performance
        import hashlib
        from livesheet.companion import encode
        performance = dict(id='score', revision='old-short-digest', pages=[dict(url='/old.webp')])
        finalize_web_performance(performance, 'a' * 32)
        first = performance['revision']
        self.assertEqual(len(first), 64)
        self.assertIn('/jobs/' + 'a' * 32 + '/files/', performance['pages'][0]['url'])
        self.assertEqual(first, hashlib.sha256(encode({k: v for k, v in performance.items() if k != 'revision'})).hexdigest())
        finalize_web_performance(performance, 'a' * 32)
        self.assertEqual(performance['revision'], first)

    def test_rolled_chord_matching_requires_explicit_mark_anchor_and_unique_unused_note(self):
        xml = ET.fromstring('''<score-partwise><part><measure>
          <note id="g"><pitch/><notations><arpeggiate/></notations></note>
          <note id="b"><chord/><pitch/></note>
          <note id="e"><chord/><pitch/></note>
          <note id="next"><pitch/></note>
        </measure></part></score-partwise>''')
        chords = arpeggiated_chords(xml)
        self.assertEqual(chords, [{'g', 'b', 'e'}])
        sources = {ident: dict(id=ident, measureIndex=1, midi=midi, part=0, staff=2,
                              voice='5', onset=0, duration=.5)
                   for ident, midi in [('g', 55), ('b', 59), ('e', 64)]}
        event = dict(measureId='m1', midi=64)
        match = lambda **kw: rolled_note_source(event, kw.get('onset', .2646), sources,
            kw.get('chords', chords), kw.get('anchors', {'g'}), kw.get('used', {'g', 'b'}))
        self.assertEqual(match()['id'], 'e')
        self.assertIsNone(match(chords=[]))
        self.assertIsNone(match(anchors=set()))
        self.assertIsNone(match(used={'g', 'b', 'e'}))
        self.assertIsNone(match(onset=.5))
        self.assertIsNone(match(onset=-.01))
        sources['next'] = dict(sources['e'], id='next', onset=.25)
        self.assertIsNone(match(), 'Never take a note beyond the next written onset')
        del sources['next']
        sources['duplicate'] = dict(sources['e'], id='duplicate', staff=1)
        self.assertIsNone(match(chords=[{'g', 'b', 'e', 'duplicate'}]))

    def test_chopin_rolled_notes_keep_timing_and_recover_left_hand_geometry(self):
        root = Path(__file__).resolve().parent
        package = json.loads((root / 'fixtures/native/mutopia-468.json').read_text())
        original = json.loads((root / 'fixtures/native/chopin-web.json').read_text())
        for ident, pitch, source in [('n4', 59, 'm2-n4'), ('n5', 64, 'm2-n5')]:
            note = next(n for n in package['notes'] if n['id'] == ident)
            old = next(n for n in original['notes'] if n['id'] == ident)
            self.assertEqual(note['midi'], pitch)
            self.assertEqual(note['sourceNoteId'], source)
            self.assertEqual(note['staff'], 1)
            self.assertEqual(note['voice'], '5')
            self.assertIsNotNone(note['box'])
            for key in ['start', 'duration', 'velocity']:
                self.assertEqual(note[key], old[key])
        self.assertTrue(all(n.get('staff') in (0, 1) for n in package['notes']))

    def test_staff_bands_use_lines_with_page_specific_sizes_and_no_overlap(self):
        def system(y, ident='1'):
            return dict(id=ident, staves=[dict(lines=[[[10, y+staff*80+i*10], [190, y+staff*80+i*10]]
                                                    for i in range(5)]) for staff in range(2)])
        mapping = dict(notes=[dict(part=0, staff=1), dict(part=0, staff=2)], pages=[
            dict(number=1, width=200, height=500, systems=[system(60), system(280, '2')]),
            dict(number=2, width=400, height=600, systems=[system(100)])])
        bands = staff_regions(mapping)
        self.assertEqual(len(bands), 6)
        self.assertEqual([r['staff'] for r in bands], [0, 1, 0, 1, 0, 1])
        self.assertEqual(bands[0]['box'], [0, .06, 1, .18])
        self.assertEqual(bands[4]['page'], 1)
        self.assertEqual(bands[4]['box'][2], .5)
        for a, b in zip(bands, bands[1:]):
            if a['page'] == b['page']:
                self.assertLessEqual(a['box'][1]+a['box'][3], b['box'][1]+1e-9)
        mapping['notes'][0].update(page=1, system='1', box=[30, 124, 10, 8])
        adjusted = staff_regions(mapping)
        self.assertGreater(adjusted[0]['box'][1] + adjusted[0]['box'][3], 132/500,
                           'A ledger note must not be cut at the staff midpoint')
        mapping['notes'][1]['part'] = 1
        self.assertEqual(staff_regions(mapping), [])
        mapping['notes'][1]['part'] = 0
        mapping['pages'][0]['systems'][0]['staves'].pop()
        self.assertEqual(staff_regions(mapping), [])

    def test_bundled_staff_geometry_covers_every_page_and_system(self):
        folder = Path(__file__).resolve().parent / 'fixtures/native'
        for ident in ['mutopia-74', 'mutopia-61', 'mutopia-468']:
            package = json.loads((folder / f'{ident}.json').read_text())
            regions = package['staffRegions']
            self.assertEqual(set(r['page'] for r in regions), set(range(len(package['pages']))))
            for region in regions:
                x, y, w, h = region['box']
                self.assertTrue(0 <= x < x+w <= 1 and 0 <= y < y+h <= 1)

    def test_bundled_notation_keeps_unique_source_ids_and_page_links(self):
        folder = Path(__file__).resolve().parent / 'fixtures/native'
        for ident in ['mutopia-74', 'mutopia-61', 'mutopia-468']:
            with self.subTest(ident=ident):
                package = json.loads((folder / f'{ident}.json').read_text())
                written = list(dict.fromkeys(m['id'] for m in package['measures']))
                self.assertEqual([m for movement in package['notation'] for m in movement['measureIDs']], written)
                for movement in package['notation']:
                    xml = ET.fromstring(base64.b64decode(movement['musicXML'], validate=True))
                    ids = [n.get('id') for n in xml.findall('.//note') if n.find('pitch') is not None]
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertNotIn(None, ids)
                    for note in movement['notes']:
                        self.assertIn(note['id'], ids)
                        self.assertIn(note['measureID'], movement['measureIDs'])
                        self.assertTrue(0 <= note['page'] < len(package['pages']))

    def test_inheritance_changes_unknown_mode_and_movement_reset(self):
        score = ET.fromstring('''<score-partwise><part>
          <measure><attributes><key><fifths>-1</fifths></key></attributes></measure>
          <measure/>
          <measure><attributes><key><fifths>-3</fifths><mode>minor</mode></key></attributes></measure>
          <measure><attributes><key><key-step>F</key-step><key-alter>1</key-alter></key></attributes></measure>
        </part></score-partwise>''')
        keys = [m['key'] for m in notation_analysis(score)]
        self.assertEqual(keys, [{'fifths': -1}, {'fifths': -1}, {'fifths': -3, 'mode': 'minor'}, None])
        self.assertIsNone(notation_analysis(ET.fromstring('<score-partwise><part><measure/></part></score-partwise>'))[0]['key'])

    def test_chords_spelling_ties_staves_and_hidden_notes(self):
        score = ET.fromstring('''<score-partwise><part><measure>
          <attributes><divisions>4</divisions></attributes>
          <note><pitch><step>B</step><alter>-1</alter><octave>4</octave></pitch><duration>4</duration><staff>1</staff></note>
          <note><chord/><pitch><step>D</step><octave>5</octave></pitch><duration>4</duration><staff>1</staff></note>
          <note><pitch><step>B</step><alter>-1</alter><octave>4</octave></pitch><duration>4</duration><tie type="stop"/><staff>1</staff></note>
          <backup><duration>8</duration></backup>
          <note><pitch><step>F</step><alter>1</alter><octave>3</octave></pitch><duration>4</duration><voice>2</voice><staff>2</staff></note>
          <note print-object="no"><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>
          <note><cue/><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration></note>
          <note><rest/><duration>4</duration></note>
        </measure></part></score-partwise>''')
        anchors = notation_analysis(score)[0]['analysisAnchors']
        self.assertEqual(anchors, [
            dict(firstMidi=70, pitchLabels=['Bb4', 'D5'], staffs=[1]),
            dict(firstMidi=54, pitchLabels=['F#3'], staffs=[2]),
            dict(firstMidi=70, pitchLabels=['Bb4'], staffs=[1]),
        ])

    def test_conflicting_part_keys_are_not_presented_as_one_key(self):
        score = ET.fromstring('''<score-partwise>
          <part><measure><attributes><key><fifths>1</fifths></key></attributes></measure></part>
          <part><measure><attributes><key><fifths>-1</fifths></key></attributes></measure></part>
        </score-partwise>''')
        self.assertIsNone(notation_analysis(score)[0]['key'])


if __name__ == '__main__':
    unittest.main()
