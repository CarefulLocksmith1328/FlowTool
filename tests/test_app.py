import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import app


class LocalFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_data, self.old_db = app.DATA, app.DB
        app.DATA = Path(self.temp.name)
        app.DB = app.DATA / "flows.db"
        app.connect().close()

    def tearDown(self):
        app.DATA, app.DB = self.old_data, self.old_db
        self.temp.cleanup()

    def sample(self):
        return {"name": "Bedingung prüfen", "enabled": True, "nodes": [
            {"id": "start", "type": "manual", "name": "Start", "config": {}},
            {"id": "test", "type": "condition", "name": "Vergleich", "config": {"left": "{{input.score}}", "operator": "greater", "right": "5"}},
            {"id": "yes", "type": "text", "name": "Ja", "config": {"value": "Ja: {{trigger.name}}"}},
            {"id": "no", "type": "text", "name": "Nein", "config": {"value": "Nein"}},
        ], "edges": [{"from": "start", "to": "test"}, {"from": "test", "to": "yes", "branch": "true"}, {"from": "test", "to": "no", "branch": "false"}]}

    def wait_for(self, flow_id):
        for _ in range(100):
            item = app.runs_for(flow_id)[0]
            if item["status"] not in ("queued", "running"):
                return item
            time.sleep(.02)
        self.fail("Lauf blieb hängen")

    def test_persistence_branch_and_expression(self):
        flow = app.save_flow(self.sample())
        self.assertEqual(app.get_flow(flow["id"])["name"], "Bedingung prüfen")
        app.start_run(flow, payload={"score": 7, "name": "Felix"})
        run = self.wait_for(flow["id"])
        self.assertEqual(run["status"], "success")
        self.assertEqual([s["node"] for s in run["steps"]], ["start", "test", "yes"])
        self.assertEqual(run["steps"][-1]["output"]["text"], "Ja: Felix")

    def test_reject_cycle_and_bad_node(self):
        flow = self.sample()
        flow["edges"].append({"from": "yes", "to": "start"})
        with self.assertRaisesRegex(ValueError, "Zyklische"):
            app.save_flow(flow)
        flow["edges"].pop()
        flow["nodes"][0]["type"] = "unknown"
        with self.assertRaisesRegex(ValueError, "Knotentyp"):
            app.save_flow(flow)

    def test_python_script_output(self):
        result = app.execute_node({"type": "script", "config": {"language": "python", "code": "output = {'value': input['value'] * 2}"}}, {"value": 3}, {"input": {"value": 3}})
        self.assertEqual(result, {"value": 6})

    def test_claude_messages_api(self):
        node = {"type": "llm", "config": {"provider": "anthropic", "model": "claude-sonnet-5", "prompt": "Hallo {{input.name}}", "system": "Antworte kurz", "max_tokens": "256"}}
        response = {"body": {"content": [{"type": "text", "text": "Hallo "}, {"type": "text", "text": "Felix"}], "model": "claude-sonnet-5", "usage": {"input_tokens": 10}}}
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-claude-key"}), patch.object(app, "http_request", return_value=response) as request:
            result = app.execute_node(node, {"name": "Felix"}, {"input": {"name": "Felix"}})
        url, method, headers, payload, timeout = request.call_args.args
        self.assertEqual(url, "https://api.anthropic.com/v1/messages")
        self.assertEqual(headers["x-api-key"], "test-claude-key")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(payload, {"model": "claude-sonnet-5", "max_tokens": 256, "system": "Antworte kurz", "messages": [{"role": "user", "content": "Hallo Felix"}]})
        self.assertEqual(result["text"], "Hallo Felix")
        self.assertEqual(result["provider"], "anthropic")

    def test_mistral_chat_api_and_missing_key(self):
        node = {"type": "llm", "config": {"provider": "mistral", "model": "mistral-small-latest", "prompt": "Test"}}
        response = {"body": {"choices": [{"message": {"content": "Antwort"}}], "model": "mistral-small-latest"}}
        with patch.dict(os.environ, {"MISTRAL_API_KEY": "test-mistral-key"}), patch.object(app, "http_request", return_value=response) as request:
            result = app.execute_node(node, {}, {"input": {}})
        url, method, headers, payload, timeout = request.call_args.args
        self.assertEqual(url, "https://api.mistral.ai/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer test-mistral-key")
        self.assertEqual(payload["messages"][-1]["content"], "Test")
        self.assertEqual(result["text"], "Antwort")
        with patch.dict(os.environ, {"MISTRAL_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "MISTRAL_API_KEY"):
                app.execute_node(node, {}, {"input": {}})

    def test_legacy_openai_flow(self):
        node = {"type": "llm", "config": {"base_url": "http://127.0.0.1:11434/v1", "model": "local", "prompt": "Test"}}
        response = {"body": {"choices": [{"message": {"content": "Lokal"}}]}}
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(app, "http_request", return_value=response) as request:
            result = app.execute_node(node, {}, {"input": {}})
        self.assertEqual(request.call_args.args[0], "http://127.0.0.1:11434/v1/chat/completions")
        self.assertNotIn("Authorization", request.call_args.args[2])
        self.assertEqual(result["text"], "Lokal")

    def test_api_security_and_webhook(self):
        flow = self.sample()
        flow["nodes"][0]["type"] = "webhook"
        flow["nodes"][0]["config"] = {"token": "test-secret-123"}
        flow = app.save_flow(flow)
        server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base = f"http://127.0.0.1:{server.server_port}"
        try:
            request = urllib.request.Request(base + "/api/flows", data=json.dumps(self.sample()).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with self.assertRaises(urllib.error.HTTPError) as rejection:
                urllib.request.urlopen(request)
            self.assertEqual(rejection.exception.code, 403)
            request = urllib.request.Request(base + f"/api/webhook/{flow['id']}/test-secret-123", data=b'{"score":8,"name":"Test"}', headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request) as response:
                self.assertEqual(response.status, 202)
            self.assertEqual(self.wait_for(flow["id"])["status"], "success")
            with urllib.request.urlopen(base + "/") as response:
                self.assertIn(b"LOCAL FLOW", response.read())
            credentials = urllib.request.Request(base + "/api/credentials", data=b'{"name":"TEST_FLOW_KEY","value":"sample-secret"}', headers={"Content-Type": "application/json", "X-Flow-Token": app.TOKEN}, method="POST")
            with urllib.request.urlopen(credentials) as response:
                self.assertEqual(response.status, 200)
                self.assertNotIn(b"sample-secret", response.read())
            self.assertEqual(os.getenv("TEST_FLOW_KEY"), "sample-secret")
        finally:
            os.environ.pop("TEST_FLOW_KEY", None)
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
