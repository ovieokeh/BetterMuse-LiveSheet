from copy import deepcopy
import unittest
import xml.etree.ElementTree as E
from livesheet.testing.check_pdf_printed_performance import printed_notation, verify_nonvelocity
from livesheet.testing.pdf_performance_reference import compare
from test_pdf_performance_reference import sample


class PrintedReferenceTests(unittest.TestCase):
    def test_only_hidden_dynamic_override_is_removed(self):
        original=E.fromstring('''<score-partwise><part><measure><direction><direction-type><dynamics><p/></dynamics></direction-type><staff>1</staff><sound dynamics="40" tempo="100"/></direction><direction><direction-type><words>Keep</words></direction-type><sound tempo="80"/></direction><note dynamics="75"><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><notations><articulations><staccato/></articulations></notations></note></measure></part></score-partwise>''')
        before=E.tostring(original);result,report=printed_notation(original)
        self.assertEqual(E.tostring(original),before)
        expected=deepcopy(original);expected.find('.//sound').attrib.pop('dynamics')
        self.assertEqual(E.tostring(result),E.tostring(expected))
        self.assertEqual(report,[dict(measureIndex=0,mark='p',value='40')])
        again,changes=printed_notation(result);self.assertEqual(changes,[]);self.assertEqual(E.tostring(again),E.tostring(result))

    def test_unprinted_or_unsupported_override_is_not_silently_dropped(self):
        for mark in ('','<dynamics><mf/></dynamics>','<dynamics><p/><f/></dynamics>'):
            root=E.fromstring('<score-partwise><part><measure><direction><direction-type>'+mark+'</direction-type><sound dynamics="40"/></direction></measure></part></score-partwise>')
            with self.subTest(mark=mark),self.assertRaises(ValueError):printed_notation(root)

    def test_reference_derivation_may_change_only_velocity(self):
        original=sample();derived=deepcopy(original);derived['notes'][0]['velocity']=40
        verify_nonvelocity(original,derived)
        # The evaluation itself still uses the unchanged strict comparator.
        self.assertFalse(compare(original,derived)['passed'])
        for field in ('pitch','onset','release','tempo','route','missing','extra','end'):
            bad=deepcopy(derived)
            if field=='pitch':bad['notes'][0]['midi']+=1
            if field=='onset':bad['notes'][0]['start']+=.1
            if field=='release':bad['notes'][0]['duration']+=.1
            if field=='tempo':bad['tempos'][0]['bpm']=80
            if field=='route':bad['measures'].reverse()
            if field=='missing':bad['notes'].pop()
            if field=='extra':bad['notes'].append(deepcopy(bad['notes'][0]))
            if field=='end':bad['duration']+=1
            with self.subTest(field=field),self.assertRaises(ValueError):verify_nonvelocity(original,bad)
