"""Device listener opt-in must retain host and token validation."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest

from livesheet.server import parse_args


class ServerConfigurationTests(unittest.TestCase):
    def rejected(self, args):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            parse_args(args)
        self.assertEqual(error.exception.code, 2)

    def test_native_options_require_explicit_host(self):
        for options in (["--native-bind", "0.0.0.0"], ["--native-name", "mac.local"],
                        ["--native-token-file", "/missing"]):
            self.rejected(options)

    def test_public_or_invalid_host_is_refused_before_token_read(self):
        for host in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "::1", "mac.local"):
            self.rejected(["--native-host", host, "--native-bind", "0.0.0.0"])

    def test_container_binding_keeps_advertised_host_and_private_key(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "key"
            path.write_text("a" * 64)
            path.chmod(0o600)
            options = ["--native-host", "192.168.1.10", "--native-name", "mac.local",
                       "--native-token-file", str(path)]
            args, token = parse_args(options + ["--native-bind", "0.0.0.0"])
            self.assertEqual(args.native_host, "192.168.1.10")
            self.assertEqual(args.native_bind, "0.0.0.0")
            self.assertEqual(token, "a" * 64)
            self.assertIsNone(parse_args(options)[0].native_bind)
            path.chmod(0o644)
            self.rejected(options)

    def test_default_is_loopback_without_native_listener(self):
        args, token = parse_args([])
        self.assertEqual(args.bind, "127.0.0.1")
        self.assertIsNone(args.native_host)
        self.assertIsNone(token)
