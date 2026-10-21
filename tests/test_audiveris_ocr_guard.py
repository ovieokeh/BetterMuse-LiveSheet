"""Exercise the actual Java guard's spatial and text exclusions."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from livesheet.pdf_pipeline.config import AudiverisRuntime
from livesheet.pdf_ocr_guard import SOURCES
from livesheet.audiveris_export_recovery import build_overlay

LAUNCHER = Path(
    os.environ.get(
        "PDF_AUDIVERIS", "/Volumes/Audiveris/Audiveris.app/Contents/MacOS/Audiveris"
    )
)


@unittest.skipUnless(LAUNCHER.exists(), "verified Audiveris distribution unavailable")
class JavaOCRGuardTests(unittest.TestCase):
    def test_source_rest_match_does_not_generalize_to_other_text_or_locations(self):
        classes = build_overlay(LAUNCHER, sources=SOURCES, cache_name="ocr-guard")
        runtime = AudiverisRuntime.from_launcher(LAUNCHER)
        code = """
import java.awt.geom.Rectangle2D;
import org.audiveris.omr.text.PrintedRestGuard;
class GuardCheck {
 static Rectangle2D box(double x,double y,double w,double h){return new Rectangle2D.Double(x,y,w,h);}
 static void check(boolean b){if(!b)throw new AssertionError();}
 public static void main(String[] args){
  for(double s:new double[]{1, 4.0/3}) {
   Rectangle2D r=box(100*s,100*s,20*s,80*s), w=box(101*s,130*s,19*s,39*s);
   check(PrintedRestGuard.matches("7",w,r));
   for(String t:new String[]{"3","6","17","7.","7 dolce",""})check(!PrintedRestGuard.matches(t,w,r));
   check(!PrintedRestGuard.matches("7",box(125*s,130*s,19*s,39*s),r));
   check(!PrintedRestGuard.matches("7",box(101*s,190*s,19*s,39*s),r));
   check(!PrintedRestGuard.matches("7",box(101*s,130*s,50*s,39*s),r));
   check(!PrintedRestGuard.matches("7",box(101*s,130*s,3*s,4*s),r));
  }
 }
}"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "GuardCheck.java"
            source.write_text(code)
            cp = os.pathsep.join([str(classes), str(runtime.jar.parent / "*")])
            subprocess.run(
                [
                    os.environ.get("PDF_COMPILER_JAVA", "java"),
                    "--class-path",
                    cp,
                    str(source),
                ],
                check=True,
                timeout=90,
                capture_output=True,
            )


if __name__ == "__main__":
    unittest.main()
