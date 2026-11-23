import http.server
import json
import pathlib
import py_compile
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import xml.etree.ElementTree as ET

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from server import SceneFoundryHandler

STRIPE_COFFEE_URL = "https://buy.stripe.com/3cIfZg27PdKo2D64Ocfw40X"
SOLANA_TIP_ADDRESS = "GG2eQbVh8ut1BK6j1A2tz7pSySdLpD38BgyEhGcxdXoK"


def _cli_bin() -> str:
    local_bin = REPO_ROOT / "bin" / "zoth-scene-foundry"
    if local_bin.is_file():
        return str(local_bin)
    return shutil.which("zoth-scene-foundry") or "/usr/local/bin/zoth-scene-foundry"


def test_python_and_js_syntax():
    for py_file in [REPO_ROOT / "server.py", REPO_ROOT / "bin" / "zoth-scene-foundry"]:
        py_compile.compile(str(py_file), doraise=True)

    node = shutil.which("node")
    if node:
        js_files = sorted((REPO_ROOT / "src").glob("*.js")) + [REPO_ROOT / "export-foundry.js"]
        for js_file in js_files:
            res = subprocess.run([node, "--check", str(js_file)], capture_output=True, text=True)
            assert res.returncode == 0, f"Syntax error in {js_file}: {res.stderr}"


def test_cli_help_version_and_status_flags():
    cli = _cli_bin()
    res_help = subprocess.run([cli, "--help"], capture_output=True, text=True)
    assert res_help.returncode == 0
    assert "zoth-scene-foundry" in res_help.stdout.lower()

    res_ver = subprocess.run([cli, "--version"], capture_output=True, text=True)
    assert res_ver.returncode == 0
    assert "1.0.0" in res_ver.stdout

    res_ui = subprocess.run([cli, "--ui"], capture_output=True, text=True)
    assert res_ui.returncode == 0
    assert "Gallery" in res_ui.stdout

    res_json = subprocess.run([cli, "--json"], capture_output=True, text=True)
    assert res_json.returncode == 0
    payload = json.loads(res_json.stdout)
    assert payload["id"] == "zoth-scene-foundry"
    assert payload["support"]["stripe_buy_coffee"] == STRIPE_COFFEE_URL

    srv_help = subprocess.run([sys.executable, str(REPO_ROOT / "server.py"), "--help"], capture_output=True, text=True)
    assert srv_help.returncode == 0

    srv_status = subprocess.run([sys.executable, str(REPO_ROOT / "server.py"), "--status"], capture_output=True, text=True)
    assert srv_status.returncode == 0
    srv_payload = json.loads(srv_status.stdout)
    assert srv_payload["ok"] is True
    assert srv_payload["tool"] == "zoth-scene-foundry"


def test_scene_foundry_http_endpoints():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SceneFoundryHandler)
    port = srv.server_port
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        for ep in ("/health", "/api/health"):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{ep}", timeout=5) as r:
                assert r.status == 200
                h = json.loads(r.read().decode())
                assert h["ok"] is True
                assert h["tool"] == "zoth-scene-foundry"
                assert h["open_access"] is True

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=5) as r:
            assert r.status == 200
            s = json.loads(r.read().decode())
            assert s["ok"] is True
            assert s["total_scenes"] >= 1000
            assert "gold" in s["tubes"]
            assert s["support"]["stripe"] == STRIPE_COFFEE_URL

        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/scenes", timeout=5) as r:
            assert r.status == 200
            sc = json.loads(r.read().decode())
            assert sc["ok"] is True
            assert isinstance(sc["scenes"], list)
    finally:
        srv.shutdown()
        srv.server_close()


def test_scene_foundry_cli_synthesis_determinism_and_tubes():
    cli = _cli_bin()
    res1 = subprocess.run([cli, "--synthesize", "42"], capture_output=True, text=True, check=True)
    res1_repeat = subprocess.run([cli, "--synthesize", "42"], capture_output=True, text=True, check=True)
    res2 = subprocess.run([cli, "--synthesize", "99"], capture_output=True, text=True, check=True)

    assert "ZOTH SCENE FOUNDRY #42" in res1.stdout
    assert "BAYER 8x8 DITHER MATRIX" in res1.stdout
    assert res1.stdout == res1_repeat.stdout
    assert res1.stdout != res2.stdout

    res_tubes = subprocess.run([cli, "--tubes"], capture_output=True, text=True, check=True)
    assert "aurum_gold" in res_tubes.stdout
    assert "cyber_cyan" in res_tubes.stdout


def test_production_guardrails():
    robots = (REPO_ROOT / "public" / "robots.txt").read_text(encoding="utf-8")
    for token in ("User-agent: *", "Googlebot", "GPTBot", "ClaudeBot", "PerplexityBot", "Sitemap:"):
        assert token in robots, f"Missing {token} in public/robots.txt"

    sitemap_xml = (REPO_ROOT / "public" / "sitemap.xml").read_text(encoding="utf-8")
    root = ET.fromstring(sitemap_xml)
    locs = [el.text for el in root.iter() if el.tag.endswith("loc") and el.text]
    assert len(locs) >= 1

    netlify = (REPO_ROOT / "netlify.toml").read_text(encoding="utf-8")
    assert 'publish = "public"' in netlify
    assert "X-Frame-Options" in netlify
    assert "X-Content-Type-Options" in netlify
    assert "Referrer-Policy" in netlify
    assert "index.html" in netlify

    html = (REPO_ROOT / "public" / "index.html").read_text(encoding="utf-8")
    ld_blocks = re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S)
    assert len(ld_blocks) >= 1
    graph = json.loads(ld_blocks[0]).get("@graph", [])
    assert len(graph) >= 2
    types = []
    for node in graph:
        t = node.get("@type")
        types.extend(t if isinstance(t, list) else [t])
    assert "WebApplication" in types or "SoftwareApplication" in types
    assert "FAQPage" in types

    assert STRIPE_COFFEE_URL in html
    assert SOLANA_TIP_ADDRESS in html
    lower_html = html.lower()
    assert "depay" not in lower_html
    assert "license-modal" not in lower_html
    assert "paywall-modal" not in lower_html
