#!/usr/bin/env python3
"""Dependency-free web chat for two people.

The app serves one HTML page and uses Server-Sent Events for live incoming
messages plus regular HTTP POST requests for outgoing messages.
"""

from __future__ import annotations

import argparse
import json
import queue
import secrets
import threading
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

ENCODING = "utf-8"
MAX_PARTICIPANTS = 2
EVENT_TIMEOUT_SECONDS = 20

INDEX_HTML = """<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Чат для двоих</title>
  <style>
    :root {
      color-scheme: light dark;
      font-family: Inter, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: #0f172a;
      color: #e2e8f0;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background:
        radial-gradient(circle at top left, rgba(59, 130, 246, 0.35), transparent 35rem),
        linear-gradient(135deg, #020617, #111827 60%, #1e293b);
    }

    main {
      width: min(920px, calc(100vw - 32px));
      height: min(720px, calc(100vh - 32px));
      display: grid;
      grid-template-rows: auto 1fr auto;
      overflow: hidden;
      border: 1px solid rgba(148, 163, 184, 0.25);
      border-radius: 24px;
      background: rgba(15, 23, 42, 0.86);
      box-shadow: 0 24px 80px rgba(0, 0, 0, 0.45);
      backdrop-filter: blur(18px);
    }

    header {
      padding: 22px 24px;
      border-bottom: 1px solid rgba(148, 163, 184, 0.2);
      display: flex;
      gap: 16px;
      justify-content: space-between;
      align-items: center;
    }

    h1 {
      margin: 0;
      font-size: clamp(1.4rem, 3vw, 2rem);
    }

    #status {
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(34, 197, 94, 0.14);
      color: #86efac;
      font-size: 0.9rem;
      white-space: nowrap;
    }

    #messages {
      padding: 24px;
      overflow-y: auto;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .message {
      max-width: 78%;
      padding: 12px 14px;
      border-radius: 18px;
      line-height: 1.4;
      word-break: break-word;
      background: rgba(30, 41, 59, 0.9);
      border: 1px solid rgba(148, 163, 184, 0.16);
    }

    .message.mine {
      align-self: flex-end;
      background: linear-gradient(135deg, #2563eb, #7c3aed);
      border-color: transparent;
      color: white;
    }

    .message.system {
      align-self: center;
      max-width: 90%;
      color: #cbd5e1;
      background: rgba(148, 163, 184, 0.12);
      font-size: 0.92rem;
    }

    .author {
      display: block;
      margin-bottom: 4px;
      font-weight: 700;
      font-size: 0.82rem;
      opacity: 0.82;
    }

    form {
      display: flex;
      gap: 12px;
      padding: 18px;
      border-top: 1px solid rgba(148, 163, 184, 0.2);
      background: rgba(2, 6, 23, 0.35);
    }

    input, button {
      border: 0;
      border-radius: 14px;
      font: inherit;
    }

    input {
      min-width: 0;
      flex: 1;
      padding: 14px 16px;
      color: #0f172a;
      background: #f8fafc;
      outline: 2px solid transparent;
    }

    input:focus { outline-color: #60a5fa; }

    button {
      padding: 0 22px;
      color: white;
      background: #2563eb;
      cursor: pointer;
      font-weight: 700;
    }

    button:disabled {
      cursor: not-allowed;
      opacity: 0.55;
    }

    @media (max-width: 640px) {
      main { height: 100vh; width: 100vw; border-radius: 0; }
      header { align-items: flex-start; flex-direction: column; }
      .message { max-width: 92%; }
      form { padding: 12px; }
      button { padding: 0 14px; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Чат для двоих</h1>
        <div>Откройте эту страницу во втором браузере или на другом устройстве.</div>
      </div>
      <div id="status">Подключение...</div>
    </header>

    <section id="messages" aria-live="polite"></section>

    <form id="chat-form">
      <input id="message-input" autocomplete="off" maxlength="1000" placeholder="Напишите сообщение..." disabled>
      <button id="send-button" type="submit" disabled>Отправить</button>
    </form>
  </main>

  <script>
    const messages = document.querySelector('#messages');
    const form = document.querySelector('#chat-form');
    const input = document.querySelector('#message-input');
    const button = document.querySelector('#send-button');
    const status = document.querySelector('#status');

    let participantId = null;
    let participantName = null;
    let events = null;

    function addMessage(event) {
      const row = document.createElement('article');
      row.className = `message ${event.kind || ''}`.trim();

      if (event.author) {
        const author = document.createElement('span');
        author.className = 'author';
        author.textContent = event.author;
        row.append(author);
      }

      const text = document.createElement('span');
      text.textContent = event.text;
      row.append(text);

      messages.append(row);
      messages.scrollTop = messages.scrollHeight;
    }

    async function joinChat() {
      participantName = prompt('Введите ваше имя:', 'Гость') || 'Гость';
      const response = await fetch('/join', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: participantName }),
      });

      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error || 'Не удалось войти в чат');
      }

      participantId = payload.id;
      status.textContent = `Вы: ${participantName}`;
      input.disabled = false;
      button.disabled = false;
      input.focus();

      events = new EventSource(`/events?id=${encodeURIComponent(participantId)}`);
      events.onmessage = (message) => addMessage(JSON.parse(message.data));
      events.onerror = () => {
        status.textContent = 'Соединение потеряно, переподключаюсь...';
      };
      events.onopen = () => {
        status.textContent = `Вы: ${participantName}`;
      };
    }

    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const text = input.value.trim();
      if (!text || !participantId) return;

      input.value = '';
      button.disabled = true;

      try {
        const response = await fetch('/send', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: participantId, text }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error || 'Сообщение не отправлено');
      } catch (error) {
        addMessage({ kind: 'system', text: error.message });
      } finally {
        button.disabled = false;
        input.focus();
      }
    });

    window.addEventListener('beforeunload', () => {
      if (participantId) {
        navigator.sendBeacon('/leave', JSON.stringify({ id: participantId }));
      }
      if (events) events.close();
    });

    joinChat().catch((error) => {
      status.textContent = 'Ошибка';
      addMessage({ kind: 'system', text: error.message });
    });
  </script>
</body>
</html>
"""


@dataclass
class Participant:
    """A connected browser participant."""

    id: str
    name: str
    events: queue.Queue[dict[str, Any]] = field(default_factory=queue.Queue)


class ChatRoom:
    """Thread-safe two-person chat state."""

    def __init__(self) -> None:
        self.participants: dict[str, Participant] = {}
        self.lock = threading.Lock()

    def join(self, name: str) -> Participant:
        with self.lock:
            self._remove_stale_locked()
            if len(self.participants) >= MAX_PARTICIPANTS:
                raise ValueError("В чате уже два участника. Попробуйте позже.")

            participant = Participant(id=secrets.token_urlsafe(16), name=name[:40] or "Гость")
            self.participants[participant.id] = participant

        self.broadcast(
            {
                "kind": "system",
                "text": f"{participant.name} вошёл(ла) в чат.",
            },
            skip_id=participant.id,
        )
        participant.events.put({"kind": "system", "text": "Вы подключились к чату."})
        return participant

    def leave(self, participant_id: str) -> None:
        with self.lock:
            participant = self.participants.pop(participant_id, None)

        if participant is not None:
            self.broadcast(
                {
                    "kind": "system",
                    "text": f"{participant.name} вышел(ла) из чата.",
                }
            )

    def send_message(self, participant_id: str, text: str) -> None:
        with self.lock:
            participant = self.participants.get(participant_id)
            if participant is None:
                raise ValueError("Вы не подключены к чату.")
            message = text[:1000]

        self.broadcast(
            {
                "kind": "mine",
                "author": participant.name,
                "text": message,
            },
            only_id=participant.id,
        )
        self.broadcast(
            {
                "kind": "",
                "author": participant.name,
                "text": message,
            },
            skip_id=participant.id,
        )

    def get_participant(self, participant_id: str) -> Participant | None:
        with self.lock:
            return self.participants.get(participant_id)

    def broadcast(
        self,
        event: dict[str, Any],
        *,
        skip_id: str | None = None,
        only_id: str | None = None,
    ) -> None:
        with self.lock:
            recipients = list(self.participants.values())

        for participant in recipients:
            if skip_id is not None and participant.id == skip_id:
                continue
            if only_id is not None and participant.id != only_id:
                continue
            participant.events.put(event)

    def _remove_stale_locked(self) -> None:
        # Participants are explicitly removed by /leave. This hook keeps the join
        # path easy to extend if heartbeat-based cleanup is added later.
        return None


class WebChatHandler(BaseHTTPRequestHandler):
    """HTTP routes for the browser chat."""

    room: ChatRoom

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html(INDEX_HTML)
            return
        if parsed.path == "/events":
            self._handle_events(parsed.query)
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        parsed = urlparse(self.path)
        if parsed.path == "/join":
            self._handle_join()
            return
        if parsed.path == "/send":
            self._handle_send()
            return
        if parsed.path == "/leave":
            self._handle_leave()
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"{self.address_string()} - {format % args}")

    def _handle_join(self) -> None:
        payload = self._read_json()
        try:
            participant = self.room.join(str(payload.get("name", "Гость")).strip())
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.CONFLICT)
            return
        self._send_json({"id": participant.id, "name": participant.name})

    def _handle_send(self) -> None:
        payload = self._read_json()
        participant_id = str(payload.get("id", ""))
        text = str(payload.get("text", "")).strip()
        if not text:
            self._send_json({"error": "Нельзя отправить пустое сообщение."}, HTTPStatus.BAD_REQUEST)
            return

        try:
            self.room.send_message(participant_id, text)
        except ValueError as error:
            self._send_json({"error": str(error)}, HTTPStatus.FORBIDDEN)
            return

        self._send_json({"ok": True})

    def _handle_leave(self) -> None:
        payload = self._read_json(allow_invalid=True)
        self.room.leave(str(payload.get("id", "")))
        self._send_json({"ok": True})

    def _handle_events(self, query: str) -> None:
        participant_id = parse_qs(query).get("id", [""])[0]
        participant = self.room.get_participant(participant_id)
        if participant is None:
            self._send_json({"error": "Unknown participant"}, HTTPStatus.FORBIDDEN)
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()

        try:
            while True:
                try:
                    event = participant.events.get(timeout=EVENT_TIMEOUT_SECONDS)
                    self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode(ENCODING))
                except queue.Empty:
                    self.wfile.write(b": keep-alive\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            self.room.leave(participant_id)

    def _read_json(self, *, allow_invalid: bool = False) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        try:
            payload = json.loads(raw_body.decode(ENCODING) or "{}")
        except json.JSONDecodeError:
            if allow_invalid:
                return {}
            raise
        return payload if isinstance(payload, dict) else {}

    def _send_html(self, html: str) -> None:
        data = html.encode(ENCODING)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode(ENCODING)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def create_handler(room: ChatRoom) -> type[WebChatHandler]:
    class ConfiguredWebChatHandler(WebChatHandler):
        pass

    ConfiguredWebChatHandler.room = room
    return ConfiguredWebChatHandler


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a two-person web chat.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/IP to bind to.")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port to listen on.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    room = ChatRoom()
    handler = create_handler(room)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Web chat is available at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping web chat...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
