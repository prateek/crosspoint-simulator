#!/usr/bin/env python3
"""Exercise the actual simulator HTTP adapter against loopback HTTP/TLS servers."""
import contextlib
import http.server
import os
from pathlib import Path
import ssl
import subprocess
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RedirectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.binary = Path(cls.temp.name) / "client"
        subprocess.run(["c++", "-std=c++17", "-I", str(ROOT / "src"),
                        str(ROOT / "tests/http-redirect-client.cpp"),
                        str(ROOT / "src/NetworkClient.cpp"), "-o", str(cls.binary)], check=True)
        cls.cert = Path(cls.temp.name) / "cert.pem"
        cls.key = Path(cls.temp.name) / "key.pem"
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(cls.key), "-out", str(cls.cert), "-days", "1",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @contextlib.contextmanager
    def server(self, routes, tls=False):
        requests = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                requests.append((self.path, dict(self.headers)))
                status, location, body = routes[self.path]
                self.send_response(status)
                if location is not None:
                    self.send_header("Location", location)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        if tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(self.cert, self.key)
            server.socket = ctx.wrap_socket(server.socket, server_side=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"{'https' if tls else 'http'}://127.0.0.1:{server.server_port}", requests
        finally:
            server.shutdown()
            server.server_close()
            thread.join()

    def fetch(self, url, downgrade=False, offset=0, abort_at=0):
        env = dict(os.environ, CURL_CA_BUNDLE=str(self.cert), NO_PROXY="127.0.0.1", no_proxy="127.0.0.1")
        output = subprocess.check_output([str(self.binary), url, str(int(downgrade)),
                                          str(offset), str(abort_at)], env=env, timeout=15, text=True)
        result, origins, body = output.split("\n", 2)
        return tuple(map(int, result.split())), origins, body

    def test_direct_and_relative_redirect_statuses(self):
        for status in (200, 301, 302, 303, 307, 308):
            with self.subTest(status=status), self.server({
                "/dir/start": (status, "book" if status != 200 else None, b"fixture" if status == 200 else b"ignore"),
                "/dir/book": (200, None, b"fixture"),
            }) as (url, requests):
                result, origins, body = self.fetch(url + "/dir/start")
                self.assertEqual(result, (200, 1, 0, 7))
                self.assertEqual(body, "fixture")
                self.assertEqual(origins, "1" if status == 200 else "11")
                self.assertTrue(all(headers.get("Authorization") for _, headers in requests))

    def test_cross_origin_strips_credentials_and_retains_range(self):
        with self.server({"/book": (206, None, b"fixture")}) as (target, received):
            with self.server({"/start": (302, target + "/book", b"ignore")}) as (url, sent):
                self.assertEqual(self.fetch(url + "/start", offset=10), ((206, 1, 0, 17), "10", "fixture"))
                self.assertIn("Authorization", sent[0][1])
                self.assertNotIn("Authorization", received[0][1])
                self.assertNotIn("X-Fixture-Secret", received[0][1])
                self.assertEqual(received[0][1]["Range"], "bytes=10-")

    def test_redirect_limit(self):
        with self.server({"/loop": (302, "/loop", b"ignore")}) as (url, requests):
            self.assertEqual(self.fetch(url + "/loop"), ((302, 0, 0, 0), "111111", ""))
            self.assertEqual(len(requests), 6)

    def test_five_redirects_succeed_and_full_response_rewinds(self):
        routes = {f"/{i}": (302, f"/{i + 1}", b"ignore") for i in range(5)}
        routes["/5"] = (200, None, b"fixture")
        with self.server(routes) as (url, requests):
            self.assertEqual(self.fetch(url + "/0", offset=10), ((200, 1, 0, 7), "111111", "fixture"))
            self.assertEqual(len(requests), 6)

    def test_error_after_redirect_does_not_write_body(self):
        with self.server({"/start": (302, "/missing", b"ignore"),
                          "/missing": (404, None, b"error")}) as (url, _):
            self.assertEqual(self.fetch(url + "/start"), ((404, 0, 0, 0), "11", ""))

    def test_missing_location(self):
        with self.server({"/start": (302, None, b"ignore")}) as (url, _):
            self.assertEqual(self.fetch(url + "/start"), ((302, 0, 0, 0), "1", ""))

    def test_abort_before_next_hop(self):
        with self.server({"/start": (302, "/book", b"ignore")}) as (url, requests):
            result, origins, body = self.fetch(url + "/start", abort_at=3)
            self.assertEqual((result, origins, body), ((302, 0, 1, 0), "1", ""))
            self.assertEqual(len(requests), 1)

    def test_https_preserved_by_default(self):
        with self.server({"/start": (302, "/book", b"ignore"),
                          "/book": (200, None, b"fixture")}, tls=True) as (url, _):
            self.assertEqual(self.fetch(url + "/start"), ((200, 1, 0, 7), "11", "fixture"))

    def test_explicit_https_to_http_rewrite(self):
        with self.server({"/book": (200, None, b"fixture")}) as (target, received):
            with self.server({"/start": (302, target.replace("http:", "https:") + "/book", b"ignore")}, tls=True) as (url, _):
                self.assertEqual(self.fetch(url + "/start", downgrade=True), ((200, 1, 0, 7), "10", "fixture"))
                self.assertNotIn("Authorization", received[0][1])
                self.assertNotIn("X-Fixture-Secret", received[0][1])


if __name__ == "__main__":
    unittest.main()
