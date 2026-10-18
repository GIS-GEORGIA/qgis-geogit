"""Unit tests for hosting.py against a local fake API (standard library only).

    python3 -m unittest discover -s tests
"""
import base64
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "geogit"))
import hosting as h  # noqa: E402

SEEN = []          # (method, path, headers, body)
ROUTES = {}        # (method, path without query) -> (status, json)


class Handler(BaseHTTPRequestHandler):
    def _serve(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length)) if length else None
        path = self.path.split("?")[0]
        # header names are case-insensitive in HTTP; urllib sends "Private-token"
        SEEN.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))
        status, data = ROUTES.get((self.command, path), (404, {"message": "not found"}))
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = _serve

    def log_message(self, *a):
        pass


class HostingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        SEEN.clear()
        ROUTES.clear()

    def last(self, method):
        return [s for s in SEEN if s[0] == method][-1]

    def test_slug(self):
        self.assertEqual(h.repo_slug("ჩემი პროექტი 2026"), "chemi-proekti-2026")
        self.assertEqual(h.repo_slug("Giorgi_Kapanadze group3 GPSV"), "giorgi_kapanadze-group3-gpsv")
        self.assertEqual(h.repo_slug("  ///  "), "qgis-project")
        self.assertEqual(h.repo_slug("Café ქართლი"), "cafe-kartli")

    def test_github_enterprise_style(self):
        ROUTES[("GET", "/api/v3/user")] = (200, {"login": "nino"})
        ROUTES[("GET", "/api/v3/user/orgs")] = (200, [{"login": "GIS-GEORGIA"}])
        ROUTES[("POST", "/api/v3/orgs/GIS-GEORGIA/repos")] = (201, {"html_url": "W", "clone_url": "C"})
        p = h.provider(h.Account("github", self.base, "tok"))
        self.assertEqual(p.check(), "nino")
        owners = p.owners()
        self.assertEqual([(o.id, o.is_user) for o in owners], [("nino", True), ("GIS-GEORGIA", False)])
        r = p.create(owners[1], "ჩემი პროექტი", "d", private=True)
        self.assertEqual((r.web_url, r.clone_url), ("W", "C"))
        _, _, headers, body = self.last("POST")
        self.assertEqual(headers["authorization"], "Bearer tok")
        self.assertEqual(headers["user-agent"], h.USER_AGENT)
        self.assertEqual(body, {"name": "chemi-proekti", "description": "d", "private": True})
        self.assertEqual(p.git_username(), "nino")
        self.assertEqual(h.GitHub(h.Account("github", "", "t")).api, "https://api.github.com")

    def test_gitlab(self):
        ROUTES[("GET", "/api/v4/user")] = (200, {"username": "nino"})
        ROUTES[("GET", "/api/v4/namespaces")] = (200, [{"id": 7, "full_path": "gis/team", "kind": "group"},
                                                       {"id": 3, "full_path": "nino", "kind": "user"}])
        ROUTES[("POST", "/api/v4/projects")] = (201, {"web_url": "W", "http_url_to_repo": "C"})
        p = h.provider(h.Account("gitlab", self.base, "tok"))
        p.check()
        owners = p.owners()
        self.assertEqual([(o.id, o.name, o.is_user) for o in owners], [("3", "nino", True), ("7", "gis/team", False)])
        p.create(owners[1], "ნაკვეთები", private=False)
        _, _, headers, body = self.last("POST")
        self.assertEqual(headers["private-token"], "tok")
        self.assertEqual(body["namespace_id"], 7)
        self.assertEqual((body["name"], body["path"], body["visibility"]), ("ნაკვეთები", "nakvetebi", "public"))
        p.create(owners[0], "x")
        self.assertNotIn("namespace_id", self.last("POST")[3])

    def test_bitbucket(self):
        p = h.provider(h.Account("bitbucket", "", "tok", email="nino@example.com"))
        p.__class__ = type("BB", (h.Bitbucket,), {"api": self.base + "/2.0"})
        ROUTES[("GET", "/2.0/user")] = (200, {"username": "nino-b"})
        ROUTES[("GET", "/2.0/user/permissions/workspaces")] = (200, {"values": [{"workspace": {"slug": "nino-b"}},
                                                                                 {"workspace": {"slug": "gis-ge"}}]})
        ROUTES[("POST", "/2.0/repositories/gis-ge/my-map")] = (200, {
            "links": {"html": {"href": "https://bitbucket.org/gis-ge/my-map"},
                      "clone": [{"name": "https", "href": "https://nino-b@bitbucket.org/gis-ge/my-map.git"},
                                {"name": "ssh", "href": "git@bitbucket.org:gis-ge/my-map.git"}]}})
        self.assertEqual(p.check(), "nino-b")
        owners = p.owners()
        r = p.create(owners[1], "My Map")
        self.assertEqual(r.clone_url, "https://bitbucket.org/gis-ge/my-map.git")      # no "user@"
        _, _, headers, body = self.last("POST")
        self.assertEqual(headers["authorization"],
                         "Basic " + base64.b64encode(b"nino@example.com:tok").decode())
        self.assertEqual(body, {"scm": "git", "is_private": True, "description": ""})
        self.assertEqual(p.git_username(), "x-bitbucket-api-token-auth")

    def test_forgejo_and_errors(self):
        ROUTES[("GET", "/api/v1/user")] = (200, {"login": "nino"})
        ROUTES[("GET", "/api/v1/user/orgs")] = (200, [{"username": "gis", "full_name": "GIS Georgia"}])
        ROUTES[("POST", "/api/v1/user/repos")] = (409, {"message": "The repository with the same name already exists."})
        p = h.provider(h.Account("forgejo", self.base + "/", "tok"))
        p.check()
        self.assertEqual(SEEN[-1][2]["authorization"], "token tok")
        owners = p.owners()
        with self.assertRaises(h.HostingError) as e:
            p.create(owners[0], "dup")
        self.assertEqual(e.exception.status, 409)
        self.assertIn("already exists", str(e.exception))
        ROUTES[("GET", "/api/v1/user")] = (401, {"message": "invalid token"})
        with self.assertRaises(h.HostingError) as e:
            p.check()
        self.assertEqual(e.exception.status, 401)

    def test_unreachable(self):
        p = h.provider(h.Account("forgejo", "http://127.0.0.1:9", "tok"))
        with self.assertRaises(h.HostingError) as e:
            p.check()
        self.assertIn("cannot reach", str(e.exception))


if __name__ == "__main__":
    unittest.main()
