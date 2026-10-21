from copy import deepcopy
import tempfile
from pathlib import Path
import unittest
import mido
from livesheet.testing.pdf_performance_reference import compare, read_midi


def sample():
    return dict(notes=[dict(midi=60,start=0.,duration=.95,velocity=80),dict(midi=64,start=1.,duration=.45,velocity=80)],
                measures=[dict(id='m0',start=0.),dict(id='m1',start=1.)],tempos=[dict(beat=0.,bpm=120.)],duration=1.45)

class PerformanceTests(unittest.TestCase):
    def test_identical_and_tiny_export_rounding_pass(self):
        a=sample();b=deepcopy(a);b['notes'][1]['start']+=.001
        self.assertTrue(compare(a,b)['passed'])
    def test_mutations_cannot_hide_in_counts_or_alignment(self):
        for kind in ('missing','extra','pitch','onset','tie-replay','hold','velocity','tempo','repeat','bar-time','ending'):
            with self.subTest(kind=kind):
                a=sample();b=deepcopy(a)
                if kind=='missing':b['notes'].pop()
                if kind=='extra':b['notes'].append(deepcopy(b['notes'][0]))
                if kind=='pitch':b['notes'][0]['midi']+=12
                if kind=='onset':b['notes'][1]['start']+=.2
                if kind=='tie-replay':b['notes'].append(dict(midi=60,start=.5,duration=.45,velocity=80))
                if kind=='hold':b['notes'][0]['duration']*=2
                if kind=='velocity':b['notes'][0]['velocity']=40
                if kind=='tempo':b['tempos'][0]['bpm']=100
                if kind=='repeat':b['measures'].reverse()
                if kind=='bar-time':b['measures'][1]['start']+=.2
                if kind=='ending':b['duration']+=.5
                self.assertFalse(compare(a,b)['passed'])
    def test_duplicate_unisons_are_not_collapsed(self):
        a=sample();a['notes'].append(deepcopy(a['notes'][0]));b=deepcopy(a);b['notes'].pop()
        self.assertEqual(len(compare(a,b)['missingAttacks']),1)
    def test_midi_tempo_and_pedal_are_read_independently(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'test.mid';m=mido.MidiFile(ticks_per_beat=480);t=mido.MidiTrack();m.tracks.append(t)
            t.extend([mido.MetaMessage('set_tempo',tempo=600000),mido.Message('control_change',control=64,value=127),
                      mido.Message('note_on',note=60,velocity=80),mido.Message('note_off',note=60,time=480),
                      mido.MetaMessage('set_tempo',tempo=300000),mido.Message('control_change',control=64,value=0,time=480)])
            m.save(p);r=read_midi(p)
            self.assertAlmostEqual(r['notes'][0]['duration'],.9);self.assertEqual([x['bpm'] for x in r['tempos']],[100,200])
    def test_unreleased_or_nonfinite_data_fails(self):
        a=sample();a['notes'][0]['start']=float('nan')
        with self.assertRaises(ValueError):compare(sample(),a)

if __name__=='__main__':unittest.main()

class RegressionTests(unittest.TestCase):
    def report(self,actual):
        result=compare(sample(),actual)
        return dict(benchmark=dict(referenceManifestSHA256='fixed'),rows=[dict(id='case',status='passed' if result['passed'] else 'mismatch',result=result)])
    def test_improvement_cannot_cancel_new_error(self):
        from livesheet.testing.pdf_performance_reference import regressions
        old=sample();old['notes'][0]['duration']=.5
        new=sample();new['notes'][1]['velocity']=30
        self.assertFalse(regressions(self.report(old),self.report(new))['passed'])
        self.assertTrue(regressions(self.report(old),self.report(sample()))['passed'])
    def test_changed_reference_or_missing_case_cannot_pass(self):
        from livesheet.testing.pdf_performance_reference import regressions
        a=self.report(sample());b=deepcopy(a);b['benchmark']['referenceManifestSHA256']='changed'
        with self.assertRaises(ValueError):regressions(a,b)
        b=deepcopy(a);b['rows']=[]
        with self.assertRaises(ValueError):regressions(a,b)
