"""Offline smoke coverage for every public tool registered in mts.py.

The network-facing tools use a deterministic in-memory HTTP opener; this suite
requires no external services or credentials.
"""
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from email.message import Message
from unittest import mock

SANDBOX = os.path.join(tempfile.gettempdir(), "mts_test_sandbox")
os.makedirs(SANDBOX, exist_ok=True)
os.environ["MCP_SANDBOX_ROOT"] = SANDBOX
os.environ["MCP_ENABLE_DANGEROUS"] = "true"
os.environ["MCP_MEMORY_FILE"] = os.path.join(SANDBOX, "memory.json")
os.environ.setdefault("MCP_LOG_LEVEL", "ERROR")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import mts  # noqa: E402


HTML_PAGE = """<!doctype html>
<html lang="en"><head><title>Offline fixture</title>
<meta name="description" content="A deterministic test page">
<meta property="og:title" content="Offline fixture">
<link rel="canonical" href="/canonical">
<link rel="alternate" type="application/rss+xml" title="Feed" href="/feed.xml">
<script type="application/ld+json">{"@type":"Article","headline":"Fixture"}</script>
</head><body><h1>Fixture heading</h1><p>Alpha content for offline testing.
<a href="/other">Related page</a><img src="/image.png" alt="fixture"></p>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>alpha</td><td>1</td></tr></table>
</body></html>"""

NEWS_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture News</title>
<item><title>Fixture news story</title><link>https://news.example/story</link>
<description>News item for the offline smoke suite.</description>
<pubDate>Mon, 01 Jan 2024 00:00:00 GMT</pubDate></item></channel></rss>"""
FEED_XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>Fixture Feed</title>
<item><title>Fixture post</title><link>https://example.test/post</link>
<description>Alpha feed content.</description></item></channel></rss>"""
SITEMAP_XML = """<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<url><loc>https://example.test/</loc><lastmod>2024-01-01</lastmod></url></urlset>"""
ARXIV_XML = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><id>https://arxiv.org/abs/2401.00001</id><title>Fixture paper</title>
<summary>Research paper summary for offline testing.</summary><published>2024-01-01T00:00:00Z</published>
<author><name>Test Author</name></author><link title="pdf" href="https://arxiv.org/pdf/2401.00001"/>
</entry></feed>"""
WIKI_JSON = json.dumps({
    "type": "standard", "title": "Fixture article", "description": "A fixture",
    "extract": "Alpha is a fixture topic. This sentence makes a useful summary for testing.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Fixture"}},
    "thumbnail": {"source": "https://img.example/wiki.png"},
})


class FakeResponse:
    def __init__(self, url, body, status=200, content_type="text/html; charset=utf-8"):
        self._url = url
        self._body = body if isinstance(body, bytes) else body.encode("utf-8")
        self._position = 0
        self.status = status
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(self._body))

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def geturl(self):
        return self._url

    def getcode(self):
        return self.status

    def read(self, size=-1):
        if size is None or size < 0:
            size = len(self._body) - self._position
        result = self._body[self._position:self._position + size]
        self._position += len(result)
        return result


class FakeOpener:
    """Small deterministic response router for tools that use urllib."""
    def __init__(self):
        self.urls = []

    def open(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.urls.append(url)
        parsed = urllib.parse.urlsplit(url)
        host, path = parsed.hostname or "", parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        ctype = "text/html; charset=utf-8"
        body = HTML_PAGE

        if host == "api.duckduckgo.com":
            body, ctype = json.dumps({
                "AbstractText": "DuckDuckGo fixture answer.", "AbstractURL": "https://example.test/answer",
                "Heading": "Fixture answer", "Type": "A", "RelatedTopics": [],
            }), "application/json"
        elif host == "duckduckgo.com" and path == "/ac/":
            body, ctype = json.dumps([{"phrase": "fixture suggestion"}]), "application/json"
        elif host == "duckduckgo.com" and path == "/":
            body = '<html><script>var vqd="12345";</script></html>'
        elif host == "duckduckgo.com" and path == "/i.js":
            body, ctype = json.dumps({"results": [{
                "title": "Fixture image", "image": "https://img.example/fixture.jpg",
                "thumbnail": "https://img.example/thumb.jpg", "url": "https://example.test/page",
                "width": 320, "height": 200,
            }]}), "application/json"
        elif host == "api.bing.com":
            body, ctype = json.dumps(["fixture", ["bing suggestion"]]), "application/json"
        elif host == "news.google.com" or (host == "www.bing.com" and path.startswith("/news/")):
            body, ctype = NEWS_XML, "application/rss+xml; charset=utf-8"
        elif host.endswith("wikipedia.org") and path.startswith("/api/rest_v1/page/summary/"):
            body, ctype = WIKI_JSON, "application/json"
        elif host.endswith("wikipedia.org") and path.endswith("/w/api.php"):
            body, ctype = json.dumps({"query": {"search": [{"title": "Fixture article"}]}}), "application/json"
        elif host == "export.arxiv.org":
            body, ctype = ARXIV_XML, "application/atom+xml; charset=utf-8"
        elif host == "api.github.com":
            body, ctype = json.dumps({"total_count": 1, "items": [{
                "full_name": "fixture/repository", "html_url": "https://github.com/fixture/repository",
                "description": "Fixture project", "stargazers_count": 7,
                "forks_count": 1, "language": "Python", "updated_at": "2024-01-01T00:00:00Z",
                "topics": ["fixture"],
            }]}), "application/json"
        elif host == "api.stackexchange.com":
            body, ctype = json.dumps({"quota_remaining": 100, "items": [{
                "title": "Fixture question", "link": "https://stackoverflow.com/q/1",
                "score": 3, "answer_count": 1, "is_answered": True,
                "tags": ["python"], "creation_date": 1704067200,
                "body": "<p>Alpha question</p>",
            }]}), "application/json"
        elif host == "dns.google":
            record_type = query.get("type", ["TXT"])[0].upper()
            body, ctype = json.dumps({"Status": 0, "Answer": [{
                "name": query.get("name", ["example.test"])[0] + ".",
                "type": {"CNAME": 5, "MX": 15, "TXT": 16, "NS": 2, "SOA": 6,
                         "CAA": 257, "SRV": 33, "PTR": 12}.get(record_type, 16),
                "TTL": 60, "data": '"fixture-record"' if record_type == "TXT" else "fixture-record",
            }]}), "application/dns-json"
        elif path == "/robots.txt":
            body, ctype = "User-agent: *\nDisallow: /private\nAllow: /\n", "text/plain; charset=utf-8"
        elif path.endswith("sitemap.xml"):
            body, ctype = SITEMAP_XML, "application/xml; charset=utf-8"
        elif path.endswith("feed.xml") or path.endswith("/rss"):
            body, ctype = FEED_XML, "application/rss+xml; charset=utf-8"
        elif path.endswith("/json"):
            body, ctype = json.dumps({"message": "fixture json", "items": [1, 2]}), "application/json"
        elif path.endswith("/download.bin"):
            body, ctype = b"fixture binary bytes", "application/octet-stream"
        return FakeResponse(url, body, content_type=ctype)


class FakeSocket:
    def __init__(self, response=b""):
        self._response = response if isinstance(response, bytes) else response.encode()
        self._position = 0
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def sendall(self, data):
        self.sent += data

    def recv(self, size):
        chunk = self._response[self._position:self._position + size]
        self._position += len(chunk)
        return chunk


class RegisteredToolsCoverageTests(unittest.TestCase):
    """Call every registered tool against deterministic local fixtures."""

    def setUp(self):
        self.root = os.path.join(SANDBOX, "tool_coverage")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root)
        os.makedirs(os.path.join(self.root, "archive-src"))
        os.makedirs(os.path.join(self.root, "git"))
        self.relative = os.path.basename(self.root)
        self._write("source.txt", "alpha beta\nalpha\n")
        self._write("replace.txt", "before\n")
        self._write("move-source.txt", "move me\n")
        self._write("delete-me.txt", "delete me\n")
        self._write("archive-src/readme.txt", "archive fixture\n")
        self._write("git/tracked.txt", "base\n")
        git_dir = os.path.join(self.root, "git")
        subprocess.run(["git", "init", "-q", git_dir], check=True)
        subprocess.run(["git", "-C", git_dir, "config", "user.name", "MTS Test"], check=True)
        subprocess.run(["git", "-C", git_dir, "config", "user.email", "mts-test@example.invalid"], check=True)
        subprocess.run(["git", "-C", git_dir, "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", git_dir, "commit", "-qm", "fixture commit"], check=True)
        self._write("git/tracked.txt", "modified\n")
        os.environ["MTS_TOOL_COVERAGE"] = "fixture-value"
        os.environ.pop("MTS_TOOL_COVERAGE_SET", None)
        self.opener = FakeOpener()
        self._cache_clear = mts._cache_clear()

    def tearDown(self):
        os.environ.pop("MTS_TOOL_COVERAGE", None)
        os.environ.pop("MTS_TOOL_COVERAGE_SET", None)
        mts._cache_clear()
        shutil.rmtree(self.root, ignore_errors=True)

    def _write(self, relative_path, content):
        path = os.path.join(self.root, relative_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    @staticmethod
    def _jwt():
        def segment(value):
            raw = json.dumps(value, separators=(",", ":")).encode()
            return base64.urlsafe_b64encode(raw).decode().rstrip("=")
        return segment({"alg": "none", "typ": "JWT"}) + "." + segment({"sub": "fixture"}) + "."

    @staticmethod
    def _fixture_search(_query, _count, _page, _safe, _time_range, _region):
        return [{"title": "Fixture result", "url": "http://example.test/page",
                 "snippet": "Alpha content from the deterministic fixture.",
                 "engine": "fixture", "rank": 1, "domain": "example.test"}]

    @staticmethod
    def _fake_connection(address, timeout=None):
        host, port = address
        if port == 43 and host == "whois.iana.org":
            return FakeSocket("domain: example.test\nrefer: whois.registry.test\n")
        if port == 43:
            return FakeSocket("Registrar: Fixture Registrar\nCreation Date: 2024-01-01\n")
        return FakeSocket()

    def test_every_registered_tool_executes_and_returns_json(self):
        self.maxDiff = None
        cases = {
            "web_search": {"query": "coverage fixture", "engines": "fixture", "fetch_content": True},
            "web_search_news": {"query": "coverage fixture", "safe_search": True},
            "web_search_images": {"query": "coverage fixture"},
            "web_search_answer": {"query": "coverage fixture"},
            "web_search_suggestions": {"query": "coverage fixture"},
            "wikipedia_lookup": {"query": "Fixture article"},
            "arxiv_search": {"query": "all:fixture"},
            "github_search": {"query": "fixture repository"},
            "stackexchange_search": {"query": "fixture question"},
            "research_topic": {"query": "coverage research", "max_sources": 2},
            "fetch_many_urls": {"urls": "http://example.test/page,http://example.test/json"},
            "url_to_markdown": {"url": "http://example.test/page"},
            "fetch_webpage_text": {"url": "http://example.test/page"},
            "http_request": {"url": "http://example.test/json"},
            "fetch_json_api": {"url": "http://example.test/json"},
            "web_download_file": {"url": "http://example.test/download.bin",
                                  "destination_filepath": f"{self.relative}/download.bin"},
            "web_scrape_links": {"url": "http://example.test/page"},
            "extract_metadata": {"url": "http://example.test/page"},
            "dns_lookup": {"domain": "localhost", "record_types": "A"},
            "check_url_status": {"url": "http://example.test/page"},
            "parse_url_headers": {"url": "http://example.test/page"},
            "read_url_hardened": {"url": "http://example.test/page"},
            "sitemap_parse": {"sitemap_url": "http://example.test/sitemap.xml"},
            "rss_feed_parse": {"feed_url": "http://example.test/feed.xml"},
            "whois_lookup": {"domain": "example.test"},
            "port_check": {"host": "127.0.0.1", "ports": "443"},
            "parse_html_document": {"html": HTML_PAGE, "base_url": "https://example.test/"},
            "extract_structured_data": {"html": HTML_PAGE, "base_url": "https://example.test/"},
            "parse_html_tables": {"html": HTML_PAGE},
            "extract_media_links": {"html": HTML_PAGE, "base_url": "https://example.test/"},
            "parse_robots_txt": {"text": "User-agent: *\nDisallow: /private\n"},
            "discover_feed_links": {"html": HTML_PAGE, "base_url": "https://example.test/"},
            "read_file": {"filepath": f"{self.relative}/source.txt"},
            "write_file": {"filepath": f"{self.relative}/written.txt", "content": "fixture write\n"},
            "edit_file_replace": {"filepath": f"{self.relative}/replace.txt",
                                  "target_snippet": "before", "replacement_snippet": "after"},
            "append_to_file": {"filepath": f"{self.relative}/written.txt", "content": "appended\n"},
            "list_directory": {"path": self.relative},
            "file_stat": {"filepath": f"{self.relative}/source.txt"},
            "delete_file": {"filepath": f"{self.relative}/delete-me.txt"},
            "make_directory": {"path": f"{self.relative}/created-dir"},
            "copy_file": {"source": f"{self.relative}/source.txt",
                          "destination": f"{self.relative}/copied.txt"},
            "move_file": {"source": f"{self.relative}/move-source.txt",
                          "destination": f"{self.relative}/moved.txt"},
            "file_checksum": {"filepath": f"{self.relative}/source.txt"},
            "search_files": {"directory": self.relative, "pattern": "*.txt"},
            "search_file_content": {"directory": self.relative, "query": "alpha"},
            "file_tree": {"path": self.relative},
            "disk_usage": {"path": self.relative},
            "compress_decompress_archive": {"archive_path": f"{self.relative}/fixture.zip",
                                            "action": "create",
                                            "target_directory": f"{self.relative}/archive-src"},
            "archive_list": {"archive_path": f"{self.relative}/fixture.zip"},
            "apply_patch": {"filepath": f"{self.relative}/patch.txt", "patch": "replacement",
                            "mode": "replace"},
            "lint_code": {"language": "python", "code": "answer = 42\n"},
            "execute_python_code": {"code": "print('python fixture')"},
            "execute_javascript_code": {"code": "console.log('javascript fixture')"},
            "execute_bash_command": {"command": "echo bash-fixture"},
            "safe_execute_python": {"code": "print(sum(range(4)))"},
            "process_list": {"max_results": 5},
            "json_parse_validate": {"json_string": '{"alpha":1}'},
            "json_query": {"json_string": '{"alpha":[{"value":42}]}',
                           "path": "alpha[0].value"},
            "regex_search": {"pattern": "(?P<word>alpha)", "text": "alpha beta alpha"},
            "base64_encode_decode": {"text": "fixture"},
            "url_encode_decode": {"text": "https://example.test/a b"},
            "hash_text": {"text": "fixture"},
            "uuid_generate": {},
            "random_string": {"length": 12, "count": 2},
            "current_datetime": {"timezone_offset_hours": 5.5},
            "timestamp_convert": {"value": "0"},
            "jwt_decode": {"token": self._jwt()},
            "csv_to_json": {"csv_text": "name,value\nalpha,1\n"},
            "json_to_csv": {"json_string": '[{"name":"alpha","value":1}]',
                            "filepath": f"{self.relative}/table.csv"},
            "yaml_json_convert": {"text": "alpha: 1"},
            "text_diff_compare": {"text1": "alpha\n", "text2": "beta\n"},
            "text_stats": {"text": "Alpha beta. Gamma delta!"},
            "text_summarize": {"text": "Alpha is a useful fixture sentence for summarization. "
                                       "Beta is another informative sentence for the summary test."},
            "parse_url": {"url": "http://127.0.0.1/path?x=1"},
            "git_status": {"directory": f"{self.relative}/git"},
            "git_diff": {"directory": f"{self.relative}/git"},
            "git_log": {"directory": f"{self.relative}/git"},
            "memory_store": {"key": "tool-coverage-entry", "value": "fixture memory", "tags": "coverage"},
            "memory_recall": {"key": "tool-coverage-entry"},
            "memory_list": {},
            "memory_delete": {"key": "tool-coverage-entry"},
            "memory_search": {"query": "fixture"},
            "ping": {},
            "health_check": {},
            "system_info": {},
            "server_stats": {},
            "self_diagnostics": {"include_network": False},
            "cache_stats": {},
            "cache_clear": {},
            "list_tools": {"category": "search"},
            "tool_help": {"name": "read_file"},
            "get_environment_variable": {"name": "MTS_TOOL_COVERAGE"},
            "set_environment_variable": {"name": "MTS_TOOL_COVERAGE_SET", "value": "done"},
        }
        self.assertEqual(set(cases), set(mts.TOOL_MAP),
                         f"Missing smoke cases: {sorted(set(mts.TOOL_MAP) - set(cases))}")

        with mock.patch.object(mts, "ALLOW_PRIVATE_NETWORKS", True), \
                mock.patch.object(mts, "_get_opener", return_value=self.opener), \
                mock.patch.object(mts, "_build_opener", return_value=self.opener), \
                mock.patch.object(mts, "_select_engines", return_value=["fixture"]), \
                mock.patch.dict(mts.SEARCH_ENGINES, {"fixture": self._fixture_search}), \
                mock.patch.object(mts, "_create_pinned_socket",
                                  side_effect=lambda host, port, timeout, *args, **kwargs:
                                  self._fake_connection((host, port), timeout)):
            results = {}
            for name in mts.TOOL_MAP:
                with self.subTest(tool=name):
                    result_text = mts.call_tool(name, cases[name])
                    try:
                        result = json.loads(result_text)
                    except Exception as exc:  # make a non-JSON return a clear test failure
                        self.fail(f"{name} returned invalid JSON: {result_text!r} ({exc})")
                    self.assertIsInstance(result, dict, name)
                    results[name] = result

        allowed_optional_errors = set()
        if not mts.HAS_YAML:
            allowed_optional_errors.add("yaml_json_convert")
        if not shutil.which("node"):
            allowed_optional_errors.add("execute_javascript_code")
        failures = {name: value for name, value in results.items()
                    if value.get("ok") is False and name not in allowed_optional_errors}
        self.assertEqual(failures, {})
        self.assertEqual(set(results), set(mts.TOOL_MAP))

    def test_dns_supports_non_address_records_offline(self):
        with mock.patch.object(mts, "ALLOW_PRIVATE_NETWORKS", True), \
                mock.patch.object(mts, "_get_opener", return_value=self.opener):
            result = json.loads(mts.call_tool("dns_lookup", {"domain": "example.test", "record_types": "TXT"}))
            ptr = json.loads(mts.call_tool("dns_lookup", {"domain": "1.1.1.1", "record_types": "PTR"}))
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["records"]["TXT"], ['"fixture-record"'])
        self.assertEqual(ptr["query_names"]["PTR"], "1.1.1.1.in-addr.arpa")
        self.assertEqual(urllib.parse.parse_qs(urllib.parse.urlsplit(self.opener.urls[-1]).query)["name"],
                         ["1.1.1.1.in-addr.arpa"])
        unsupported = json.loads(mts.call_tool("dns_lookup", {"domain": "example.test",
                                                                "record_types": "AXFR"}))
        self.assertIn("error", unsupported)
        self.assertEqual(unsupported["unsupported"], ["AXFR"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
