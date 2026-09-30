"""Local Flow Studio: dependency-free local flow editor and executor."""
from __future__ import annotations

import argparse
import base64
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "FlowTool"
        if os.name == "nt" and getattr(sys, "frozen", False) else ROOT / "data")
DB = DATA / "flows.db"
WEB = ROOT / "web"
HOST = "127.0.0.1"
PORT = 8765
DESKTOP_MODE = False
TOKEN = secrets.token_urlsafe(32)
MAX_BODY = 2_000_000
MAX_NODES = 200
EXPR = re.compile(r"{{\s*([^{}]+?)\s*}}")
NODE_TYPES = {"manual", "webhook", "schedule", "text", "set", "condition", "http", "llm", "script", "github", "docker", "delay", "output"}
FLOW_DRAFT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "notes": {"type": "string"},
        "nodes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"id": {"type": "string"}, "type": {"type": "string", "enum": sorted(NODE_TYPES)},
                           "name": {"type": "string"}, "config_json": {"type": "string"}},
            "required": ["id", "type", "name", "config_json"]}},
        "edges": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                           "branch": {"type": "string", "enum": ["true", "false"]}},
            "required": ["from", "to", "branch"]}},
    },
    "required": ["name", "notes", "nodes", "edges"],
}
FLOW_ASSISTANT_INSTRUCTIONS = """Erstelle einen ausführbaren Entwurf für Local Flow Studio als JSON. Beginne mit genau einem Startknoten (normalerweise manual). Verwende nur unterstützte Typen: manual, webhook, schedule, text, set, condition, http, llm, script, github, docker, delay, output. Jeder Knoten erhält eine kurze eindeutige ID, einen verständlichen Namen und config_json als JSON-Objekt im String. Verbinde die Knoten zu einem gerichteten azyklischen Graphen. Verwende bei Bedingungs-Ausgängen branch true oder false, sonst true. Konfiguration: text {\"value\":\"...\"}; set {\"value\":\"{\\\"feld\\\":\\\"wert\\\"}\"}; condition {\"left\":\"{{input.feld}}\",\"operator\":\"equals\",\"right\":\"...\"}; http {\"url\":\"https://...\",\"method\":\"GET\"}; llm {\"provider\":\"openai\" oder \"anthropic\",\"prompt\":\"{{input.text}}\"}; output {}. Für fehlende Details nutze Platzhalter und erkläre sie in notes. Keine echten Geheimnisse in die Konfiguration schreiben. Erzeuge keine Ausführung; der Nutzer prüft den Entwurf vor dem Speichern und Starten."""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def uid():
    return uuid.uuid4().hex[:12]


def connect():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE IF NOT EXISTS flows (id TEXT PRIMARY KEY, name TEXT NOT NULL, body TEXT NOT NULL, updated TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, status TEXT NOT NULL, trigger TEXT NOT NULL, steps TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, error TEXT)")
    conn.commit()
    return conn


@contextlib.contextmanager
def database():
    conn = connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def jsonable(value):
    try:
        return json.loads(json.dumps(value, default=str))
    except (TypeError, ValueError):
        return str(value)


def validate_flow(flow):
    if not isinstance(flow, dict):
        raise ValueError("Flow muss ein Objekt sein")
    nodes, edges = flow.get("nodes"), flow.get("edges")
    if not isinstance(nodes, list) or not isinstance(edges, list) or len(nodes) > MAX_NODES or len(edges) > 400:
        raise ValueError("Ungültige Anzahl von Knoten oder Verbindungen")
    ids = [n.get("id") for n in nodes if isinstance(n, dict)]
    if len(ids) != len(nodes) or len(ids) != len(set(ids)) or any(not isinstance(i, str) or not i for i in ids):
        raise ValueError("Knoten-IDs fehlen oder sind doppelt")
    if any(n.get("type") not in NODE_TYPES or not isinstance(n.get("config", {}), dict) for n in nodes):
        raise ValueError("Unbekannter Knotentyp oder ungültige Konfiguration")
    for node in nodes:
        if node["type"] == "webhook" and not node.setdefault("config", {}).get("token"):
            node["config"]["token"] = secrets.token_urlsafe(24)
    indegree = {i: 0 for i in ids}
    links = {i: [] for i in ids}
    for edge in edges:
        if not isinstance(edge, dict) or edge.get("from") not in indegree or edge.get("to") not in indegree:
            raise ValueError("Verbindung verweist auf unbekannten Knoten")
        indegree[edge["to"]] += 1
        links[edge["from"]].append(edge["to"])
    queue = [i for i, d in indegree.items() if d == 0]
    count = 0
    while queue:
        key = queue.pop()
        count += 1
        for target in links[key]:
            indegree[target] -= 1
            if indegree[target] == 0:
                queue.append(target)
    if count != len(nodes):
        raise ValueError("Zyklische Flows werden noch nicht unterstützt")
    flow["name"] = str(flow.get("name") or "Unbenannter Flow")[:120]
    flow["enabled"] = bool(flow.get("enabled", False))
    return flow


def save_flow(flow):
    flow = validate_flow(flow)
    flow["id"] = str(flow.get("id") or uid())
    with database() as db:
        db.execute("INSERT INTO flows(id,name,body,updated) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,body=excluded.body,updated=excluded.updated", (flow["id"], flow["name"], json.dumps(flow, ensure_ascii=False), now()))
    return flow


def get_flow(flow_id):
    with database() as db:
        row = db.execute("SELECT body FROM flows WHERE id=?", (flow_id,)).fetchone()
    return json.loads(row["body"]) if row else None


def list_flows():
    with database() as db:
        rows = db.execute("SELECT id,name,body,updated FROM flows ORDER BY updated DESC").fetchall()
    return [{"id": r["id"], "name": r["name"], "enabled": json.loads(r["body"]).get("enabled", False), "updated": r["updated"]} for r in rows]


def resolve_path(obj, path):
    for part in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def render(value, context):
    if isinstance(value, list):
        return [render(x, context) for x in value]
    if isinstance(value, dict):
        return {k: render(v, context) for k, v in value.items()}
    if not isinstance(value, str):
        return value
    whole = EXPR.fullmatch(value)
    if whole:
        return resolve_path(context, whole.group(1).strip())
    return EXPR.sub(lambda m: str(resolve_path(context, m.group(1).strip()) or ""), value)


def parse_json_field(raw, default):
    if raw is None or raw == "":
        return default
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(raw)


def http_request(url, method="GET", headers=None, body=None, timeout=20):
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL muss mit http:// oder https:// beginnen")
    data = None
    if body is not None and method.upper() not in ("GET", "HEAD"):
        data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, headers=headers or {}, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=min(max(float(timeout), 1), 120)) as response:
            raw = response.read(2_000_000).decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = raw
            return {"status": response.status, "body": parsed, "headers": dict(response.headers)}
    except urllib.error.HTTPError as error:
        body = error.read(2000).decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {error.code}: {body}") from error


def generate_flow(description, provider, model=""):
    description = str(description).strip()
    if not 8 <= len(description) <= 4000:
        raise ValueError("Beschreibe den Flow mit 8 bis 4000 Zeichen")
    defaults = {"openai": ("OPENAI_API_KEY", "gpt-4o-mini"),
                "anthropic": ("ANTHROPIC_API_KEY", "claude-sonnet-5")}
    if provider not in defaults:
        raise ValueError("Wähle ChatGPT oder Claude")
    variable, default_model = defaults[provider]
    key = os.getenv(variable, "")
    if not key:
        raise ValueError(f"{variable} fehlt. Setze den API-Schlüssel unter Verbindungen")
    model = str(model or default_model).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,100}", model):
        raise ValueError("Ungültige Modell-ID")

    if provider == "openai":
        payload = {"model": model, "instructions": FLOW_ASSISTANT_INSTRUCTIONS,
                   "input": description,
                   "text": {"format": {"type": "json_schema", "name": "flow_draft",
                                       "strict": True, "schema": FLOW_DRAFT_SCHEMA}}}
        result = http_request("https://api.openai.com/v1/responses", "POST",
                              {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                              payload, 90)["body"]
        if result.get("status") == "incomplete":
            raise RuntimeError("ChatGPT hat den Entwurf nicht vollständig ausgegeben")
        content = result.get("output_text") or "".join(
            part.get("text", "") for item in result.get("output", []) if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text")
    else:
        payload = {"model": model, "max_tokens": 4096,
                   "system": FLOW_ASSISTANT_INSTRUCTIONS,
                   "messages": [{"role": "user", "content": description}],
                   "output_config": {"format": {"type": "json_schema", "schema": FLOW_DRAFT_SCHEMA}}}
        result = http_request("https://api.anthropic.com/v1/messages", "POST",
                              {"x-api-key": key, "anthropic-version": "2023-06-01",
                               "Content-Type": "application/json"}, payload, 90)["body"]
        if result.get("stop_reason") == "max_tokens":
            raise RuntimeError("Claude hat den Entwurf nicht vollständig ausgegeben")
        content = "".join(part.get("text", "") for part in result.get("content", [])
                          if part.get("type") == "text")
    if not content:
        raise RuntimeError("Das Modell hat keinen Flow-Entwurf zurückgegeben")
    try:
        draft = json.loads(content)
    except ValueError as error:
        raise ValueError("Der Modell-Entwurf enthält kein gültiges JSON") from error
    if not isinstance(draft, dict) or not isinstance(draft.get("nodes"), list) or not isinstance(draft.get("edges"), list):
        raise ValueError("Der Modell-Entwurf hat ein ungültiges Format")
    if not 1 <= len(draft["nodes"]) <= 30 or len(draft["edges"]) > 60:
        raise ValueError("Der Modell-Entwurf ist zu groß oder leer")
    nodes = []
    for index, raw in enumerate(draft["nodes"]):
        if not isinstance(raw, dict) or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", str(raw.get("id", ""))):
            raise ValueError("Der Modell-Entwurf enthält eine ungültige Knoten-ID")
        config_text = raw.get("config_json", "{}")
        if not isinstance(config_text, str) or len(config_text) > 6000:
            raise ValueError("Ungültige Knoten-Konfiguration")
        try:
            config = json.loads(config_text)
        except ValueError as error:
            raise ValueError("Ungültiges JSON in einer Knoten-Konfiguration") from error
        if not isinstance(config, dict):
            raise ValueError("Knoten-Konfiguration muss ein Objekt sein")
        nodes.append({"id": raw["id"], "type": raw.get("type"),
                      "name": str(raw.get("name") or raw["id"])[:80], "config": config,
                      "x": 95 + (index % 4) * 250, "y": 180 + (index // 4) * 160})
    if sum(node["type"] in ("manual", "webhook", "schedule") for node in nodes) != 1:
        raise ValueError("Der Entwurf braucht genau einen Startknoten")
    flow = {"id": None, "name": str(draft.get("name") or "KI-Entwurf")[:120],
            "enabled": False, "nodes": nodes, "edges": draft["edges"]}
    validate_flow(flow)
    return {"flow": flow, "notes": str(draft.get("notes") or "")[:2000],
            "provider": provider, "model": model}


def git_command(args, cwd=None, timeout=60, github_auth=False):
    if not shutil.which("git"):
        raise RuntimeError("Git ist nicht installiert")
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    with contextlib.ExitStack() as stack:
        if github_auth and env.get("GITHUB_TOKEN"):
            import tempfile
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            script = Path(directory) / ("askpass.bat" if os.name == "nt" else "askpass.sh")
            if os.name == "nt":
                script.write_text('@echo off\necho %~1 | findstr /I "Username" >nul\nif %ERRORLEVEL% EQU 0 (echo x-access-token) else (echo %GITHUB_TOKEN%)\n')
            else:
                script.write_text('#!/bin/sh\ncase "$1" in *Username*) echo x-access-token;; *) printf "%s\\n" "$GITHUB_TOKEN";; esac\n')
                script.chmod(0o700)
            env["GIT_ASKPASS"] = str(script)
            env["GIT_ASKPASS_REQUIRE"] = "force"
        proc = subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout or "Git-Fehler").strip()[:1000])
    return proc.stdout.strip()


def safe_repo(name):
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", name or "") or name in (".", ".."):
        raise ValueError("Ungültiger Repository-Name")
    path = DATA / "repos" / name
    if not (path / ".git").exists():
        raise ValueError("Repository noch nicht verbunden")
    return path


def docker_command(args, timeout=30):
    if not shutil.which("docker"):
        raise RuntimeError("Docker ist nicht installiert")
    proc = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode:
        raise RuntimeError((proc.stderr or proc.stdout).strip()[:1000])
    return proc.stdout.strip()


def execute_node(node, value, context):
    kind = node["type"]
    cfg = render(node.get("config", {}), context)
    if kind in ("manual", "webhook", "schedule"):
        return value
    if kind == "text":
        return {"text": cfg.get("value", "")}
    if kind == "set":
        return parse_json_field(cfg.get("value"), {})
    if kind == "output":
        return value
    if kind == "condition":
        left, right = cfg.get("left"), cfg.get("right")
        operator = cfg.get("operator", "equals")
        conditions = {"equals": lambda: left == right, "not_equals": lambda: left != right, "contains": lambda: str(right) in str(left), "exists": lambda: left is not None and left != "", "greater": lambda: float(left) > float(right), "less": lambda: float(left) < float(right)}
        if operator not in conditions:
            raise ValueError("Unbekannter Vergleich")
        return {"match": bool(conditions[operator]()), "value": value}
    if kind == "delay":
        time.sleep(min(max(float(cfg.get("seconds", 1)), 0), 60))
        return value
    if kind == "http":
        headers = parse_json_field(cfg.get("headers"), {})
        body = cfg.get("body")
        if cfg.get("body_json"):
            body = parse_json_field(body, {})
            headers.setdefault("Content-Type", "application/json")
        return http_request(str(cfg.get("url", "")), cfg.get("method", "GET"), headers, body, cfg.get("timeout", 20))
    if kind == "llm":
        provider = str(cfg.get("provider") or "openai")
        providers = {
            "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-4o-mini"),
            "anthropic": ("https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", "claude-sonnet-5"),
            "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY", "mistral-small-latest"),
        }
        if provider not in providers:
            raise ValueError("Unbekannter LLM-Anbieter")
        default_base, default_key, default_model = providers[provider]
        base = str(cfg.get("base_url") or default_base).rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise ValueError("Ungültige LLM-Basis-URL")
        variable = str(cfg.get("key_env") or default_key)
        key = os.getenv(variable, "")
        if provider != "openai" and not key:
            raise ValueError(f"API-Schlüssel fehlt: {variable} unter Verbindungen setzen")
        model = cfg.get("model") or default_model
        system = str(cfg.get("system") or "Du bist ein hilfreicher Assistent.")
        prompt = str(cfg.get("prompt") or value)
        headers = {"Content-Type": "application/json"}
        timeout = cfg.get("timeout", 60)
        if provider == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
            limit = int(cfg.get("max_tokens") or 1024)
            if limit < 1 or limit > 128000:
                raise ValueError("Max. Ausgabetoken müssen zwischen 1 und 128000 liegen")
            payload = {"model": model, "max_tokens": limit, "system": system, "messages": [{"role": "user", "content": prompt}]}
            result = http_request(base + "/messages", "POST", headers, payload, timeout)["body"]
            content = result.get("content", [])
            output = "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")
        else:
            if key:
                headers["Authorization"] = "Bearer " + key
            payload = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
            result = http_request(base + "/chat/completions", "POST", headers, payload, timeout)["body"]
            content = result["choices"][0]["message"]["content"]
            output = content if isinstance(content, str) else "".join(chunk.get("text", "") for chunk in content if isinstance(chunk, dict) and chunk.get("type") == "text")
        if not output:
            raise RuntimeError("Das Modell hat keinen Text zurückgegeben; Tool-Aufrufe werden in diesem Knoten noch nicht verarbeitet")
        return {"text": output, "model": result.get("model"), "usage": result.get("usage"), "provider": provider}
    if kind == "script":
        language = cfg.get("language", "python")
        code = str(cfg.get("code") or "output = input")
        if language == "python":
            wrapper = "import json,sys,contextlib\ninput=json.load(sys.stdin)\noutput=None\nwith contextlib.redirect_stdout(sys.stderr):\n exec(compile(" + repr(code) + ", '<flow-script>', 'exec'))\nprint(json.dumps(output, default=str))"
            interpreter = shutil.which("python") if getattr(sys, "frozen", False) else sys.executable
            if not interpreter:
                raise RuntimeError("Python-Skripte benötigen eine separate Python-Installation")
            command = [interpreter, "-I", "-c", wrapper]
        elif language == "javascript":
            if not shutil.which("node"):
                raise RuntimeError("Node.js ist nicht installiert")
            wrapper = "let input=JSON.parse(require('fs').readFileSync(0,'utf8')); let output=null; console.log=(...v)=>process.stderr.write(v.join(' ')+'\\n'); (async()=>{" + code + "\n})().then(()=>process.stdout.write(JSON.stringify(output))).catch(e=>{console.error(e);process.exit(1)})"
            command = ["node", "-e", wrapper]
        else:
            raise ValueError("Skriptsprache nicht unterstützt")
        DATA.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(command, input=json.dumps(value), capture_output=True, text=True, timeout=min(float(cfg.get("timeout") or 30), 120), cwd=DATA)
        if proc.returncode:
            raise RuntimeError((proc.stderr or "Skriptfehler")[-2000:])
        return json.loads(proc.stdout or "null")
    if kind == "github":
        repo = safe_repo(str(cfg.get("repo") or ""))
        action = cfg.get("action", "status")
        if action == "status":
            return {"repo": repo.name, "status": git_command(["status", "--short", "--branch"], repo)}
        if action == "pull":
            return {"repo": repo.name, "result": git_command(["pull", "--ff-only"], repo, github_auth=True)}
        if action == "commit_push":
            git_command(["add", "-A"], repo)
            status = git_command(["status", "--porcelain"], repo)
            if status:
                git_command(["-c", "user.name=Local Flow Studio", "-c", "user.email=local-flow@localhost", "commit", "-m", str(cfg.get("message") or "Update via Local Flow Studio")], repo)
            result = git_command(["push"], repo, github_auth=True)
            return {"repo": repo.name, "result": result or "Push abgeschlossen", "committed": bool(status)}
        raise ValueError("Unbekannte Git-Aktion")
    if kind == "docker":
        container = str(cfg.get("container") or "")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", container):
            raise ValueError("Ungültiger Container-Name")
        command = cfg.get("command") or ""
        import shlex
        args = shlex.split(command, posix=os.name != "nt")
        if not args:
            raise ValueError("Container-Befehl fehlt")
        return {"container": container, "stdout": docker_command(["exec", container, *args], timeout=min(float(cfg.get("timeout") or 30), 120))}
    raise ValueError("Knotentyp nicht unterstützt")


def flow_order(flow):
    nodes = {n["id"]: n for n in flow["nodes"]}
    incoming = {i: [] for i in nodes}
    outgoing = {i: [] for i in nodes}
    for e in flow["edges"]:
        incoming[e["to"]].append(e)
        outgoing[e["from"]].append(e)
    indegree = {i: len(incoming[i]) for i in nodes}
    queue = [i for i in nodes if indegree[i] == 0]
    order = []
    while queue:
        i = queue.pop(0)
        order.append(i)
        for e in outgoing[i]:
            indegree[e["to"]] -= 1
            if indegree[e["to"]] == 0:
                queue.append(e["to"])
    return nodes, incoming, order


def update_run(run_id, status, steps, error=None, finish=False):
    with database() as db:
        db.execute("UPDATE runs SET status=?,steps=?,error=?,finished=? WHERE id=?", (status, json.dumps(jsonable(steps), ensure_ascii=False), error, now() if finish else None, run_id))


def run_flow(flow, run_id, trigger_id, payload):
    steps = []
    update_run(run_id, "running", steps)
    try:
        nodes, incoming, order = flow_order(flow)
        outputs, active = {}, {trigger_id}
        for node_id in order:
            if node_id not in active:
                continue
            node = nodes[node_id]
            inputs = [outputs[e["from"]] for e in incoming[node_id] if e["from"] in outputs and (nodes[e["from"]]["type"] != "condition" or e.get("branch", "true") == ("true" if outputs[e["from"]]["match"] else "false"))]
            value = payload if node_id == trigger_id else (inputs[0] if len(inputs) == 1 else inputs)
            context = {"input": value, "trigger": payload, "nodes": outputs, "env": dict(os.environ)}
            stamp = now()
            try:
                result = jsonable(execute_node(node, value, context))
                outputs[node_id] = result
                steps.append({"node": node_id, "name": node.get("name", node["type"]), "status": "success", "output": result, "at": stamp})
                for e in flow["edges"]:
                    if e["from"] == node_id and (node["type"] != "condition" or e.get("branch", "true") == ("true" if result["match"] else "false")):
                        active.add(e["to"])
                update_run(run_id, "running", steps)
            except Exception as error:
                steps.append({"node": node_id, "name": node.get("name", node["type"]), "status": "error", "error": str(error), "at": stamp})
                raise
        update_run(run_id, "success", steps, finish=True)
    except Exception as error:
        update_run(run_id, "error", steps, str(error), finish=True)


def start_run(flow, trigger_type="manual", payload=None, trigger_id=None):
    trigger = next((n for n in flow["nodes"] if n["id"] == trigger_id and n["type"] == trigger_type), None) if trigger_id else next((n for n in flow["nodes"] if n["type"] == trigger_type), None)
    if trigger is None:
        if trigger_type != "manual":
            raise ValueError("Passender Startknoten fehlt")
        trigger = next((n for n in flow["nodes"] if n["type"] in ("webhook", "schedule")), None)
    if trigger is None:
        raise ValueError("Füge zuerst einen Startknoten hinzu")
    run_id = uid()
    with database() as db:
        db.execute("INSERT INTO runs(id,flow_id,status,trigger,steps,started) VALUES(?,?,?,?,?,?)", (run_id, flow["id"], "queued", trigger_type, "[]", now()))
    threading.Thread(target=run_flow, args=(flow, run_id, trigger["id"], payload if payload is not None else {}), daemon=True).start()
    return run_id


def runs_for(flow_id):
    with database() as db:
        rows = db.execute("SELECT * FROM runs WHERE flow_id=? ORDER BY started DESC LIMIT 30", (flow_id,)).fetchall()
    return [dict(r) | {"steps": json.loads(r["steps"])} for r in rows]


def scheduler(stop):
    due = {}
    while not stop.wait(1):
        present = set()
        for summary in list_flows():
            if not summary["enabled"]:
                continue
            flow = get_flow(summary["id"])
            for node in flow["nodes"]:
                if node["type"] != "schedule":
                    continue
                key = flow["id"] + ":" + node["id"]
                present.add(key)
                try:
                    interval = min(max(float(node.get("config", {}).get("interval", 60)), 5), 86400)
                except (ValueError, TypeError):
                    print(f"Ungültiges Intervall in Flow {flow['id']}", file=sys.stderr)
                    continue
                if key not in due:
                    due[key] = time.monotonic() + interval
                if time.monotonic() >= due[key]:
                    try:
                        start_run(flow, "schedule", {"scheduled_at": now()}, node["id"])
                    except Exception as error:
                        print("Zeitplanfehler:", error, file=sys.stderr)
                    due[key] = time.monotonic() + interval
        due = {k: v for k, v in due.items() if k in present}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print("[%s] %s" % (self.log_date_time_string(), fmt % args))

    def reply(self, status, value):
        raw = json.dumps(value, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 0 or size > MAX_BODY:
            raise ValueError("Anfrage zu groß")
        return json.loads(self.rfile.read(size) or b"{}")

    def secure(self):
        origin = self.headers.get("Origin", "")
        if origin and origin != f"http://{HOST}:{self.server.server_port}":
            self.reply(403, {"error": "Fremder Ursprung"})
            return False
        if self.headers.get("X-Flow-Token") != TOKEN:
            self.reply(403, {"error": "Sitzungsschlüssel fehlt"})
            return False
        return True

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/meta":
                return self.reply(200, {"desktop_mode": DESKTOP_MODE})
            if path == "/api/flows":
                return self.reply(200, list_flows())
            if path.startswith("/api/flows/"):
                flow = get_flow(path.split("/")[3])
                return self.reply(200 if flow else 404, flow or {"error": "Flow nicht gefunden"})
            if path.startswith("/api/runs/"):
                return self.reply(200, runs_for(path.split("/")[3]))
            if path == "/api/connections":
                return self.reply(200, {"git": bool(shutil.which("git")), "github_token": bool(os.getenv("GITHUB_TOKEN")), "docker": bool(shutil.which("docker")), "node": bool(shutil.which("node")), "python": sys.version.split()[0], "python_scripts": bool(shutil.which("python")) if getattr(sys, "frozen", False) else True, "credentials": sorted(k for k in os.environ if (k.endswith("_KEY") or k.endswith("_TOKEN")) and k not in ("FLOW_TOKEN",))})
            if path == "/api/github/me":
                key = os.getenv("GITHUB_TOKEN")
                if not key:
                    raise ValueError("GITHUB_TOKEN fehlt in der Umgebung")
                result = http_request("https://api.github.com/user", headers={"Authorization": "Bearer " + key, "Accept": "application/vnd.github+json", "User-Agent": "Local-Flow-Studio"})
                return self.reply(200, {"login": result["body"].get("login")})
            if path == "/api/github/repos":
                folder = DATA / "repos"
                return self.reply(200, [p.name for p in folder.iterdir() if (p / ".git").exists()] if folder.exists() else [])
            if path == "/api/docker":
                result = docker_command(["ps", "--format", "{{.Names}}"], 10)
                return self.reply(200, result.splitlines() if result else [])
            if path == "/" or path in ("/app.js", "/style.css"):
                filename = "index.html" if path == "/" else path.lstrip("/")
                content = (WEB / filename).read_bytes()
                if filename == "index.html":
                    content = content.replace(b"__FLOW_TOKEN__", TOKEN.encode())
                self.send_response(200)
                self.send_header("Content-Type", {"index.html": "text/html", "app.js": "application/javascript", "style.css": "text/css"}[filename] + "; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                return self.wfile.write(content)
            self.reply(404, {"error": "Nicht gefunden"})
        except Exception as error:
            self.reply(400, {"error": str(error)})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            if path.startswith("/api/webhook/"):
                parts = path.split("/")
                if len(parts) != 5:
                    return self.reply(404, {"error": "Webhook nicht gefunden"})
                flow = get_flow(parts[3])
                trigger = next((n for n in flow["nodes"] if n["type"] == "webhook" and parts[4] and secrets.compare_digest(str(n.get("config", {}).get("token", "")), parts[4])), None) if flow else None
                if not flow or not flow.get("enabled") or not trigger:
                    return self.reply(404, {"error": "Webhook nicht gefunden oder deaktiviert"})
                return self.reply(202, {"run_id": start_run(flow, "webhook", self.body(), trigger["id"])})
            if not self.secure():
                return
            if path == "/api/shutdown" and DESKTOP_MODE:
                self.reply(200, {"stopping": True})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            if path == "/api/flows":
                return self.reply(200, save_flow(self.body()))
            if path == "/api/assistant/generate":
                data = self.body()
                return self.reply(200, generate_flow(data.get("description", ""), data.get("provider", ""), data.get("model", "")))
            if path == "/api/credentials":
                data = self.body()
                name, value = str(data.get("name", "")).strip(), str(data.get("value", ""))
                if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,60}(?:_KEY|_TOKEN)", name) or not value or len(value) > 8000:
                    raise ValueError("Variablenname muss mit _KEY oder _TOKEN enden; Wert darf nicht leer sein")
                os.environ[name] = value
                return self.reply(200, {"name": name, "set": True})
            if path.startswith("/api/run/"):
                flow = get_flow(path.split("/")[3])
                if not flow:
                    return self.reply(404, {"error": "Flow nicht gefunden"})
                return self.reply(202, {"run_id": start_run(flow, payload=self.body())})
            if path == "/api/github/clone":
                data = self.body()
                owner = str(data.get("owner", ""))
                repo = str(data.get("repo", ""))
                if not re.fullmatch(r"[\w.-]{1,100}", owner) or not re.fullmatch(r"[\w.-]{1,100}", repo) or repo in (".", ".."):
                    raise ValueError("GitHub-Nutzer und Repository prüfen")
                target = DATA / "repos" / repo
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise ValueError("Repository ist bereits verbunden")
                git_command(["clone", f"https://github.com/{owner}/{repo}.git", str(target)], github_auth=True, timeout=120)
                return self.reply(200, {"repo": repo})
            return self.reply(404, {"error": "Nicht gefunden"})
        except Exception as error:
            self.reply(400, {"error": str(error)})

    def do_DELETE(self):
        if not self.secure():
            return
        path = urllib.parse.urlparse(self.path).path
        if path.startswith("/api/flows/"):
            key = path.split("/")[3]
            with database() as db:
                db.execute("DELETE FROM flows WHERE id=?", (key,))
            return self.reply(200, {"deleted": key})
        self.reply(404, {"error": "Nicht gefunden"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    connect().close()
    stop = threading.Event()
    threading.Thread(target=scheduler, args=(stop,), daemon=True).start()
    server = ThreadingHTTPServer((HOST, args.port), Handler)
    print(f"Local Flow Studio: http://{HOST}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == "__main__":
    main()
