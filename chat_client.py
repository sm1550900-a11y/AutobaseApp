#!/usr/bin/env python3
"""Terminal client for the two-person TCP chat."""

from __future__ import annotations

import argparse
import socket
import sys
import threading

ENCODING = "utf-8"
BUFFER_SIZE = 4096


def receive_messages(connection: socket.socket, stop_event: threading.Event) -> None:
    """Print incoming messages until the server closes the connection."""
    while not stop_event.is_set():
        try:
            data = connection.recv(BUFFER_SIZE)
        except OSError:
            break

        if not data:
            print("\n[client] Server disconnected.")
            stop_event.set()
            break

        print(data.decode(ENCODING).strip())


def send_user_input(connection: socket.socket, stop_event: threading.Event) -> None:
    """Read terminal input and send it to the server."""
    while not stop_event.is_set():
        try:
            message = input()
        except (EOFError, KeyboardInterrupt):
            message = "/quit"

        try:
            connection.sendall(f"{message}\n".encode(ENCODING))
        except OSError:
            stop_event.set()
            break

        if message == "/quit":
            stop_event.set()
            break


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Connect to a two-person TCP chat server.")
    parser.add_argument("--host", default="127.0.0.1", help="Server host/IP to connect to.")
    parser.add_argument("--port", type=int, default=5000, help="Server TCP port.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    stop_event = threading.Event()

    try:
        with socket.create_connection((args.host, args.port)) as connection:
            receiver = threading.Thread(
                target=receive_messages,
                args=(connection, stop_event),
                daemon=True,
            )
            receiver.start()
            send_user_input(connection, stop_event)
    except ConnectionRefusedError:
        print("[client] Could not connect. Start chat_server.py first.", file=sys.stderr)
        return 1
    except OSError as error:
        print(f"[client] Connection error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
