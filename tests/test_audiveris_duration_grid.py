"""Exercise the actual patched Java exporter, including exactness failures.

Requires the pinned local Audiveris distribution and cached Java compiler.
"""
from pathlib import Path
import subprocess
import os
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livesheet import audiveris_export_recovery as recovery

LAUNCHER = Path(os.environ.get('PDF_AUDIVERIS', '/Volumes/Audiveris/Audiveris.app/Contents/MacOS/Audiveris'))


@unittest.skipUnless(LAUNCHER.exists(), 'requires pinned local Audiveris distribution')
class DurationGridTests(unittest.TestCase):
    def test_actual_exporter_preserves_rational_durations(self):
        classes = recovery.build_overlay(LAUNCHER)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)/'DurationGridCheck.java'
            source.write_text('''
import java.lang.reflect.*;
import org.audiveris.omr.math.Rational;
public class DurationGridCheck {
    public static void main(String[] args) {
        try { check(); System.exit(0); }
        catch (Throwable failure) { failure.printStackTrace(); System.exit(1); }
    }
    private static void check() throws Exception {
        Method duration = Class.forName("org.audiveris.omr.score.PartwiseBuilder")
            .getDeclaredMethod("exactExportDuration", Rational.class, int.class);
        duration.setAccessible(true);
        // num, den, original grid, exact output ticks. Normal grids are unchanged.
        int[][] cases = {{1,4,2,1},{1,2,2,2},{1,1,2,4},{1,4,1,1},
            {1,1,1,4},{1,6,6,2},{1,3,6,4},{3,8,8,3},
            {1,4,480,120},{3,8,480,180},{0,1,2,0},{-1,2,2,-2}};
        for (int[] c : cases) {
            int actual = (Integer)duration.invoke(null, new Rational(c[0],c[1]),c[2]);
            if (actual != c[3]) throw new AssertionError("Wrong duration: " + actual);
        }
        int[][] invalid = {{1,5,2},{1,4,0},{1,4,-2},{2147483647,1,480}};
        for (int[] c : invalid) {
            try {
                duration.invoke(null,new Rational(c[0],c[1]),c[2]);
                throw new AssertionError("Accepted inexact, invalid or overflowing duration");
            } catch (InvocationTargetException e) {
                if (!(e.getCause() instanceof IllegalArgumentException) &&
                    !(e.getCause() instanceof ArithmeticException)) throw e;
            }
        }
    }
}
''')
            cp = ':'.join([str(classes)] + [str(p) for p in sorted(recovery.AudiverisRuntime.from_launcher(LAUNCHER).jar.parent.glob('*.jar'))])
            compile_result = subprocess.run(['java', '-jar', str(Path(os.environ.get('PDF_EXPORT_CACHE', str(recovery.BUILD)))/'ecj-3.44.0.jar'),
                '-17', '-proc:none', '-nowarn', '-cp', cp, '-d', tmp, str(source)],
                capture_output=True, text=True, timeout=60)
            self.assertEqual(compile_result.returncode, 0, compile_result.stdout+compile_result.stderr)
            java = recovery.AudiverisRuntime.from_launcher(LAUNCHER).java
            result = subprocess.run([str(java), '-Djava.awt.headless=true', '-cp', tmp+':'+cp, 'DurationGridCheck'], capture_output=True,
                                    text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout+result.stderr)


if __name__ == '__main__':
    unittest.main()
