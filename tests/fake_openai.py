"""A stand-in for the OpenAI API (POST /v1/chat/completions, GET /v1/models/{id}). No real key or network."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.server.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": json.loads(self.rfile.read(length))})
        replies = self.server.replies
        status, body = replies.pop(0) if len(replies) > 1 else replies[0]
        self._send(status, body)

    def do_GET(self):
        self.server.requests.append({"path": self.path, "auth": self.headers.get("Authorization"), "body": None})
        self._send(*self.server.models_reply)

    def log_message(self, *args):
        pass


def completion(content, finish_reason="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal:
        message = {"role": "assistant", "content": None, "refusal": refusal}
    return 200, {"choices": [{"index": 0, "finish_reason": finish_reason, "message": message}]}


def verdict(outcome="pass", reason="ok", quote=""):
    return completion(json.dumps({"outcome": outcome, "reason": reason, "quote": quote}))


def clauses(items):
    return completion(json.dumps({"clauses": items}))


class FakeOpenAI:
    def __init__(self, reply=None):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.replies = [reply or verdict()]
        self.server.models_reply = (200, {"id": "gpt-4o-mini", "object": "model"})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    @property
    def requests(self):
        return self.server.requests

    def reply(self, *replies):
        self.server.replies = list(replies)

    def models(self, status, body):
        self.server.models_reply = (status, body)

    def close(self):
        self.server.shutdown()
        self.server.server_close()
