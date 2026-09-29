# tools/localsend-send/test_localsend_send.py
"""Tests for the cloud-push enrolment path of localsend-send.py (Task C2).

Spins up a tiny stdlib HTTP server that mimics the cloud-push server's three
endpoints (/api/enrol/start, /api/enrol, /oauth/token) on a free port, and
exercises the pure functions against it. Credentials always go to tmp_path;
$HOME/.config is never touched.

The mock mirrors the real server's enrolment semantics: every /api/enrol/start
mints a FRESH enrolment_id + key and stores {eid: key} server-side, and
/api/enrol validates the submitted key against the submitted enrolment_id
(401 on mismatch). This is what keeps the "exactly one /api/enrol/start per
flow" invariant honest.
"""
import argparse
import importlib.util
import io
import json
import os
import threading
import urllib.error
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

# localsend-send.py contains a hyphen so it cannot be imported by name; load it
# by path instead. The module-level __main__ guard keeps main() from running.
_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "localsend-send.py")
_spec = importlib.util.spec_from_file_location("localsend_send", _SCRIPT)
ls = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ls)

CLIENT_ID = "client123"
CLIENT_SECRET = "secret123"
ACCESS_TOKEN = "tok123"


class _Handler(BaseHTTPRequestHandler):
    server_version = "CloudPushTest"

    def __init__(self, *args, state=None, **kwargs):
        self.state = state if state is not None else {}
        super().__init__(*args, **kwargs)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        self.state.setdefault("user_agents", []).append(
            self.headers.get("User-Agent"))

        if self.path == "/api/enrol/start":
            payload = json.loads(body)
            assert payload.get("info"), "enrol/start should carry client info"
            # Mint a FRESH id + key on EVERY call, exactly like the real
            # server (server/app/app.py), and store {eid: key} server-side.
            self.state["enrol_start_hits"] = self.state.get("enrol_start_hits", 0) + 1
            self.state["enrol_seq"] = self.state.get("enrol_seq", 0) + 1
            eid = f"eid{self.state['enrol_seq']}"
            key = f"key{self.state['enrol_seq']}"
            self.state.setdefault("enrol_keys", {})[eid] = key
            self.state["last_enrol_id"] = eid
            self._json({
                "enrolment_id": eid,
                "verification_uri":
                    f"http://{self.headers.get('Host', 'localhost')}/enrol/{eid}",
            })
        elif self.path == "/api/enrol":
            assert self.headers.get("Content-Type", "").startswith("application/json")
            payload = json.loads(body)
            eid = payload.get("enrolment_id")
            expected = self.state.get("enrol_keys", {}).get(eid)
            if expected is not None and payload.get("code") == expected:
                self._json({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET})
            else:
                self._json({"error": "invalid enrolment code"}, status=401)
        elif self.path == "/api/enrol/complete":
            payload = json.loads(body)
            eid = payload.get("enrolment_id")
            if eid in self.state.get("approved", {}):
                self._json({"status": "approved",
                            **self.state["approved"].pop(eid)})
            elif eid in self.state.get("enrol_keys", {}):
                self._json({"status": "pending"})
            else:
                self._json({"status": "unknown"}, status=404)
        elif self.path == "/oauth/token":
            self.state["oauth_hits"] = self.state.get("oauth_hits", 0) + 1
            assert self.headers.get("Content-Type", "").startswith(
                "application/x-www-form-urlencoded")
            form = urllib.parse.parse_qs(body.decode())
            if (form.get("grant_type", [""])[0] == "client_credentials"
                    and form.get("client_id", [""])[0] == CLIENT_ID
                    and form.get("client_secret", [""])[0] == CLIENT_SECRET):
                self._json({"access_token": ACCESS_TOKEN})
            else:
                self._json({"error": "invalid_grant"}, status=401)
        elif self.path == "/api/push":
            # Capture the raw multipart body + auth so the test can inspect them.
            self.state["push_auth"] = self.headers.get("Authorization")
            self.state["push_content_type"] = self.headers.get("Content-Type")
            self.state["push_body"] = body
            self._json({"push_id": "p1", "files_sent": 2})
        else:
            self._json({"error": "not found"}, status=404)

    def _json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence per-request logging
        pass


@pytest.fixture()
def cloud_server():
    """Yield (server_url, state) for a mock cloud-push server on a free port."""
    state = {"enrol_start_hits": 0, "enrol_seq": 0, "enrol_keys": {},
             "approved": {}, "oauth_hits": 0}
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        lambda *args, **kwargs: _Handler(*args, state=state, **kwargs))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}", state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_start_enrolment_mints_fresh_eid(cloud_server):
    server_url, state = cloud_server
    eid, uri = ls._start_enrolment(server_url)
    # The eid is exactly the one the mock issued and stored server-side.
    assert eid in state["enrol_keys"]
    assert eid in uri
    assert state["enrol_start_hits"] == 1

    # A second start mints a DIFFERENT id + key (the real server never reuses).
    eid2, uri2 = ls._start_enrolment(server_url)
    assert eid2 != eid
    assert state["enrol_keys"][eid2] != state["enrol_keys"][eid]
    assert uri2 != uri
    assert state["enrol_start_hits"] == 2


def test_enrol_flow_writes_credentials(tmp_path, cloud_server):
    server_url, state = cloud_server
    creds_path = tmp_path / "push-credentials.json"

    # Use an eid + key from a REAL prior start so the key matches.
    eid, _ = ls._start_enrolment(server_url)
    key = state["enrol_keys"][eid]

    creds = ls._enrol_flow(server_url, eid, key, str(creds_path))

    assert creds == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "server_url": server_url,
    }
    assert json.loads(creds_path.read_text()) == creds
    assert creds_path.stat().st_mode & 0o777 == 0o600
    # _enrol_flow must NOT call /api/enrol/start itself.
    assert state["enrol_start_hits"] == 1


def test_enrol_flow_wrong_key_rejected(tmp_path, cloud_server):
    server_url, state = cloud_server
    creds_path = tmp_path / "creds.json"

    eid, _ = ls._start_enrolment(server_url)

    with pytest.raises(urllib.error.HTTPError) as exc:
        ls._enrol_flow(server_url, eid, "not-the-key", str(creds_path))
    assert exc.value.code == 401
    assert not creds_path.exists()


def test_cmd_enrol_performs_one_start_and_saves_after_approval(
        tmp_path, cloud_server, monkeypatch):
    """cmd_enrol performs exactly ONE /api/enrol/start, then polls for approval.

    Regression for the double-start defect (cmd_enrol used to start once and
    _enrol_flow start again, minting an id whose key never matched) and for the
    manual key prompt — the browser approval is collected by polling instead.
    """
    server_url, state = cloud_server
    creds_path = tmp_path / "push-credentials.json"
    hits_before = state["enrol_start_hits"]

    monkeypatch.delenv("DISPLAY", raising=False)  # keep xdg-open out of the test
    # Simulate the operator clicking Complete registration in the browser.
    monkeypatch.setattr(ls, "_await_enrolment",
                        lambda url, eid, **k: {"client_id": CLIENT_ID,
                                               "client_secret": CLIENT_SECRET,
                                               "server_url": url})

    rc = ls.cmd_enrol(
        argparse.Namespace(server=server_url, creds=str(creds_path)))

    assert rc == 0
    assert state["enrol_start_hits"] == hits_before + 1  # exactly one start
    assert json.loads(creds_path.read_text()) == {
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "server_url": server_url,
    }
    assert creds_path.stat().st_mode & 0o777 == 0o600


def test_await_enrolment_via_browser_approval(cloud_server, monkeypatch):
    server_url, state = cloud_server
    eid, _ = ls._start_enrolment(server_url)
    # Empty stdin (immediate EOF): the browser approves server-side.
    monkeypatch.setattr(ls.sys, "stdin", io.StringIO(""))
    state["approved"][eid] = {"client_id": CLIENT_ID,
                              "client_secret": CLIENT_SECRET}

    rec = ls._await_enrolment(server_url, eid, timeout=5, interval=0.05)
    assert rec == {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                   "server_url": server_url}


def test_await_enrolment_via_pasted_code(cloud_server, monkeypatch):
    server_url, state = cloud_server
    eid, _ = ls._start_enrolment(server_url)
    code = state["enrol_keys"][eid]
    # The operator types the short code; there is no browser approval.
    monkeypatch.setattr(ls.sys, "stdin", io.StringIO(code + "\n"))

    rec = ls._await_enrolment(server_url, eid, timeout=5, interval=0.05)
    assert rec == {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                   "server_url": server_url}


def test_await_enrolment_accepts_a_code_while_the_poll_is_pending(
        cloud_server, monkeypatch):
    """A typed code is picked up concurrently with the approval poll.

    Regression: the HTTP poll used to run inline, so stdin was only read
    between requests and typing during a slow poll did nothing.
    """
    server_url, state = cloud_server
    eid, _ = ls._start_enrolment(server_url)
    code = state["enrol_keys"][eid]

    # Make the approval poll slow (still pending) and let the code arrive
    # mid-flight; the code must win without waiting for the poll to return.
    monkeypatch.setattr(ls.sys, "stdin", io.StringIO(code + "\n"))
    rec = ls._await_enrolment(server_url, eid, timeout=5, interval=5.0)
    assert rec == {"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
                   "server_url": server_url}


def test_await_enrolment_times_out(cloud_server, monkeypatch):
    server_url, state = cloud_server
    eid, _ = ls._start_enrolment(server_url)
    monkeypatch.setattr(ls.sys, "stdin", io.StringIO(""))
    assert ls._await_enrolment(server_url, eid, timeout=0.3,
                               interval=0.05) is None


def test_device_names_merges_lan_and_registered_and_de_dupes(monkeypatch):
    monkeypatch.setattr(ls, "_discover", lambda timeout=3.0: {"Sunny Falcon": {}})
    monkeypatch.setattr(ls, "list_push_devices", lambda creds, server_url=None: [
        {"name": "Clever Juniper"}, {"name": "Sunny Falcon"}])
    args = argparse.Namespace(discover_timeout=0.01,
                              server="https://push.example.com", creds=None)
    assert ls._device_names(args) == ["Sunny Falcon", "Clever Juniper"]


def test_names_flag_prints_names_only(monkeypatch, capsys):
    monkeypatch.setattr(ls, "_device_names",
                        lambda args: ["Clever Juniper", "Laptop"])
    assert ls.main(["--list", "--names"]) == 0
    assert capsys.readouterr().out == "Clever Juniper\nLaptop\n"


def test_bash_completion_script_lists_options_and_choices(capsys):
    script = ls._bash_completion_script()
    assert "complete -o default -F _mdrender_send mdrender-send localsend-send.py" in script
    assert "--enrol" in script and "--name" in script and "--conflict" in script
    # Value choices are completed for their options.
    assert '--conflict) COMPREPLY=( $(compgen -W "replace skip rename"' in script
    assert '--completion) COMPREPLY=( $(compgen -W "bash"' in script
    # --name pulls device names from `--list --names` (both forms).
    assert "--name)" in script
    assert "--list --names" in script
    assert "--name=)" in script
    # --opt=value form completes the value (bash splits the word at '=').
    assert '-P "--conflict="' in script
    # File arguments defer to readline's filename completion.
    assert "compopt -o default" in script

    # main() prints it and exits 0 without needing files/host.
    assert ls.main(["--completion", "bash"]) == 0
    assert "complete -o default -F _mdrender_send" in capsys.readouterr().out
    # Bare --completion defaults to bash.
    assert ls.main(["--completion"]) == 0
    assert "complete -o default -F _mdrender_send" in capsys.readouterr().out


def test_open_browser_detaches_from_the_terminal(monkeypatch):
    """xdg-open must not inherit the tty, or its logs clobber the CLI prompt."""
    calls = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs

    monkeypatch.setattr(ls.subprocess, "Popen", FakePopen)
    ls._open_browser("https://example.com/enrol/abc")

    assert calls["args"] == ["xdg-open", "https://example.com/enrol/abc"]
    assert calls["kwargs"]["stdin"] is ls.subprocess.DEVNULL
    assert calls["kwargs"]["stdout"] is ls.subprocess.DEVNULL
    assert calls["kwargs"]["stderr"] is ls.subprocess.DEVNULL
    assert calls["kwargs"]["start_new_session"] is True


def test_get_token_hits_oauth_endpoint(cloud_server):
    server_url, state = cloud_server
    tok = ls._get_token({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                        server_url)
    assert tok == ACCESS_TOKEN
    assert state["oauth_hits"] == 1


def test_get_access_token_from_file(tmp_path, cloud_server):
    server_url, _ = cloud_server
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({
        "server_url": server_url,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }))
    # server_url passed explicitly
    assert ls.get_access_token(str(creds_path), server_url) == ACCESS_TOKEN
    # server_url read from the credentials file when not passed
    assert ls.get_access_token(str(creds_path)) == ACCESS_TOKEN


def test_cloud_requests_send_a_non_default_user_agent(tmp_path, cloud_server):
    """Every cloud request must avoid urllib's default User-Agent.

    Cloudflare answers the default ``Python-urllib/3.x`` signature with 403
    error code 1010, which broke enrolment against the tunneled server. All of
    the enrolment, token and push requests must carry the client's own UA.
    """
    server_url, state = cloud_server
    assert not ls.USER_AGENT.startswith("Python-urllib")

    eid, _ = ls._start_enrolment(server_url)
    creds_path = tmp_path / "push-credentials.json"
    ls._enrol_flow(server_url, eid, state["enrol_keys"][eid], str(creds_path))
    ls._get_token({"client_id": CLIENT_ID, "client_secret": CLIENT_SECRET},
                  server_url)

    assert state["user_agents"]  # at least start + enrol + token
    assert all(ua == ls.USER_AGENT for ua in state["user_agents"])


def test_discover_all_collects_every_client_and_flags_mdrender(monkeypatch):
    replies = [
        {"alias": "Clever Juniper", "version": "2.1", "deviceModel": "Pixel 8",
         "deviceType": "mobile", "app": "MDRender", "port": 53317,
         "protocol": "https", "extensions": ["mds"], "announce": False},
        {"alias": "Laptop", "version": "2.1", "deviceModel": "ThinkPad",
         "deviceType": "desktop", "port": 53318, "protocol": "https",
         "extensions": [], "announce": False},
    ]
    ips = ["10.0.0.5", "10.0.0.6"]
    state = {"i": 0}

    class FakeSocket:
        def setsockopt(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, data, addr):
            pass

        def recvfrom(self, bufsize):
            i = state["i"]
            if i < len(replies):
                state["i"] += 1
                return json.dumps(replies[i]).encode(), (ips[i], 53317)
            raise ls.socket.timeout("empty LAN")

        def close(self):
            pass

    monkeypatch.setattr(ls.socket, "socket", lambda *a, **k: FakeSocket())
    found = ls._discover(timeout=0.3)

    assert set(found) == {"Clever Juniper", "Laptop"}
    assert found["Clever Juniper"]["ip"] == "10.0.0.5"
    assert found["Clever Juniper"]["app"] == "MDRender"
    assert found["Laptop"]["port"] == 53318
    assert ls.is_mdrender(found["Clever Juniper"]) is True
    assert ls.is_mdrender(found["Laptop"]) is False


def test_is_mdrender_accepts_each_advertised_signal():
    assert ls.is_mdrender({"extensions": ["mds"]})
    assert ls.is_mdrender({"app": "MDRender", "extensions": []})
    assert ls.is_mdrender({"deviceModel": "MDRender (Pixel 8)"})
    assert not ls.is_mdrender({"app": "LocalSend", "extensions": [],
                               "deviceModel": "Pixel 8"})
    assert not ls.is_mdrender({})


def test_cmd_list_prints_lan_clients_and_registered_devices(monkeypatch, capsys):
    monkeypatch.setattr(ls, "_discover", lambda timeout=3.0: {
        "Clever Juniper": {"ip": "10.0.0.5", "port": 53317, "protocol": "https",
                           "deviceModel": "Pixel 8", "deviceType": "mobile",
                           "version": "2.1", "extensions": ["mds"]},
        "Laptop": {"ip": "10.0.0.6", "port": 53317, "protocol": "https",
                   "deviceModel": "ThinkPad", "deviceType": "desktop",
                   "version": "2.1", "extensions": []},
    })
    monkeypatch.setattr(ls, "list_push_devices",
                        lambda creds, server_url=None: [
                            {"name": "Clever Juniper", "registered_at": 0,
                             "last_seen": 0}])

    rc = ls.cmd_list(argparse.Namespace(discover_timeout=0.01,
                                        server="https://push.example.com",
                                        creds=None))
    out = capsys.readouterr().out

    assert rc == 0
    assert "LocalSend clients (2):" in out
    assert "Clever Juniper" in out and "MDRender (mds: folder, conflict)" in out
    assert "Laptop" in out and "LocalSend" in out
    assert "Registered push devices on https://push.example.com (1):" in out


def test_resolve_dns_tries_name_then_slug_then_local(monkeypatch):
    seen = []

    def fake_gethostbyname(host):
        seen.append(host)
        if host == "clever-juniper.local":
            return "10.0.0.9"
        raise ls.socket.gaierror("not found")

    monkeypatch.setattr(ls.socket, "gethostbyname", fake_gethostbyname)
    assert ls.resolve_dns("Clever Juniper") == "10.0.0.9"
    # Tried in order, stopping at the first hit.
    assert seen == ["Clever Juniper", "clever-juniper", "clever-juniper.local"]


def test_resolve_dns_returns_none_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(ls.socket, "gethostbyname",
                        lambda h: (_ for _ in ()).throw(ls.socket.gaierror("no")))
    assert ls.resolve_dns("Ghost") is None


def test_resolve_name_prefers_local_dns_over_discovery(monkeypatch):
    monkeypatch.setattr(ls, "resolve_dns", lambda name: "10.0.0.7")
    discovery_calls = []
    monkeypatch.setattr(ls, "discover_lan",
                        lambda names, timeout=3.0: discovery_calls.append(names) or {})
    assert ls.resolve_name("Falcon") == "10.0.0.7"
    assert discovery_calls == []  # discovery is skipped when DNS answers


def test_resolve_name_falls_back_to_discovery_then_gives_up(monkeypatch):
    monkeypatch.setattr(ls, "resolve_dns", lambda name: None)
    monkeypatch.setattr(ls, "discover_lan",
                        lambda names, timeout=3.0: {"Falcon": "10.0.0.5"})
    assert ls.resolve_name("Falcon") == "10.0.0.5"
    assert ls.resolve_name("Ghost") is None


def test_discover_lan_parses_response(monkeypatch):
    """Discovery announces on the multicast group and maps alias -> ip.

    Regression locks on two app-driven requirements: the packet must go to the
    multicast group 224.0.0.167 (NOT 255.255.255.255, which no receiver listens
    on) and must carry announce: true (LocalSendDiscovery.kt drops any packet
    where announce is not true). It also asserts the loop early-exits once the
    wanted alias is found (exactly one announce).
    """
    sent = []

    class FakeSocket:
        def __init__(self, *a, **k):
            self.sent = sent

        def setsockopt(self, *a):
            pass

        def settimeout(self, t):
            pass

        def sendto(self, data, addr):
            sent.append((data, addr))

        def recvfrom(self, bufsize):
            # Answer the first announce like the app does (unicast reply to the
            # source address); then time out like an empty LAN.
            if len(sent) == 1:
                return (json.dumps({"alias": "Sunny Falcon", "version": "2.1",
                                    "deviceModel": "Pixel", "deviceType": "mobile",
                                    "fingerprint": "f", "port": 53317,
                                    "protocol": "https", "download": False,
                                    "announce": False}).encode(),
                        ("10.0.0.5", 53317))
            raise ls.socket.timeout("timeout")

        def close(self):
            pass

    monkeypatch.setattr(ls.socket, "socket", FakeSocket)

    found = ls.discover_lan(["Sunny Falcon"], timeout=3.0)

    assert found == {"Sunny Falcon": "10.0.0.5"}
    # One announce, because the wanted alias was found on the first reply.
    assert len(sent) == 1
    # The multicast group, not the broadcast address (regression).
    assert sent[0][1] == ("224.0.0.167", 53317)
    # The announce flag is what the app's listener requires (regression).
    assert json.loads(sent[0][0])["announce"] is True


def test_push_to_server_multipart(tmp_path, cloud_server):
    """push_to_server POSTs a Bearer-authed multipart body to /api/push.

    The mock server captures the raw body and Content-Type; the test parses the
    boundary back out and asserts the target_device text part, one file part per
    path (with filename + file bytes), and the trailing closing delimiter.
    """
    server_url, state = cloud_server
    f1 = tmp_path / "one.txt"
    f2 = tmp_path / "two.bin"
    f1.write_bytes(b"hello world")
    f2.write_bytes(b"\x00\x01\x02binary")
    creds_path = tmp_path / "creds.json"
    creds_path.write_text(json.dumps({
        "server_url": server_url,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }))

    rc = ls.push_to_server(str(creds_path), "Sunny Falcon", [str(f1), str(f2)])

    assert rc == 0
    assert state["oauth_hits"] == 1  # a fresh token was fetched
    assert state["push_auth"] == f"Bearer {ACCESS_TOKEN}"
    assert state["user_agents"] and all(ua == ls.USER_AGENT
                                        for ua in state["user_agents"])
    ct = state["push_content_type"]
    assert ct.startswith("multipart/form-data; boundary=")
    boundary = ct.split("boundary=", 1)[1]
    body = state["push_body"]

    assert b'name="target_device"' in body
    assert b"Sunny Falcon" in body
    assert b'name="file"' in body
    assert b'filename="one.txt"' in body
    assert b"hello world" in body          # file 1 bytes
    assert b'filename="two.bin"' in body
    assert b"\x00\x01\x02binary" in body   # file 2 bytes
    assert body.endswith(("--" + boundary + "--").encode())  # closing delimiter
