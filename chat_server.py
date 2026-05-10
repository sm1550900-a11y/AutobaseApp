#!/usr/bin/env python3
"""TCP chat server for exactly two people.

Run this script first, then connect two clients with chat_client.py.
The server relays every message from one connected client to the other.
"""

from __future__ import annotations

import argparse
import socket
import threading
from dataclasses import dataclass

ENCODING = "utf-8"
MAX_CLIENTS = 2
BUFFER_SIZE = 4096


@dataclass
class Client:
    """Connected chat participant."""

    name: str
    connection: socket.socket
    address: tuple[str, int]


def receive_line(connection: socket.socket) -> str:
    """Receive one newline-terminated UTF-8 line from a socket."""
    chunks: list[bytes] = []
    while True:
        chunk = connection.recv(1)
        if not chunk:
            raise ConnectionError("client disconnected")
        if chunk == b"\n":
            return b"".join(chunks).decode(ENCODING).strip()
        chunks.append(chunk)


def send_line(connection: socket.socket, message: str) -> None:
    """Send one newline-terminated UTF-8 line to a socket."""
    connection.sendall(f"{message}\n".encode(ENCODING))


class TwoPersonChatServer:
    """Small threaded chat server that accepts up to two clients."""

    def __init__(self) -> None:
        self.clients: list[Client] = []
        self.clients_lock = threading.Lock()
        self.shutdown_event = threading.Event()

    def broadcast(self, sender: Client | None, message: str) -> None:
        """Send a message to all clients except the sender."""
        with self.clients_lock:
            recipients = [client for client in self.clients if client is not sender]

        for client in recipients:
            try:
                send_line(client.connection, message)
            except OSError:
                self.remove_client(client)

    def remove_client(self, client: Client) -> None:
        """Remove a disconnected client and notify the remaining participant."""
        removed = False
        with self.clients_lock:
            if client in self.clients:
                self.clients.remove(client)
                removed = True

        if removed:
            print(f"{client.name} disconnected from {client.address}")
            self.broadcast(None, f"[server] {client.name} left the chat.")

        try:
            client.connection.close()
        except OSError:
            pass

    def handle_client(self, client: Client) -> None:
        """Relay messages from one client to the other client."""
        try:
            send_line(client.connection, "[server] Connected. Type /quit to leave.")
            self.broadcast(client, f"[server] {client.name} joined the chat.")

            while not self.shutdown_event.is_set():
                data = client.connection.recv(BUFFER_SIZE)
                if not data:
                    break

                message = data.decode(ENCODING).strip()
                if not message:
                    continue
                if message == "/quit":
                    break

                formatted = f"{client.name}: {message}"
                print(formatted)
                self.broadcast(client, formatted)
        except (ConnectionError, OSError):
            pass
        finally:
            self.remove_client(client)

    def accept_clients(self, host: str, port: int) -> None:
        """Start listening and serve clients until interrupted."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind((host, port))
            server_socket.listen(MAX_CLIENTS)
            print(f"Chat server is listening on {host}:{port}")
            print("Waiting for two people to connect...")

            while not self.shutdown_event.is_set():
                connection, address = server_socket.accept()

                with self.clients_lock:
                    if len(self.clients) >= MAX_CLIENTS:
                        send_line(connection, "[server] Chat is full. Try again later.")
                        connection.close()
                        continue

                try:
                    send_line(connection, "Enter your name:")
                    name = receive_line(connection) or f"User{len(self.clients) + 1}"
                except (ConnectionError, OSError):
                    connection.close()
                    continue

                client = Client(name=name, connection=connection, address=address)
                with self.clients_lock:
                    self.clients.append(client)

                print(f"{client.name} connected from {client.address}")
                thread = threading.Thread(target=self.handle_client, args=(client,), daemon=True)
                thread.start()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a two-person TCP chat server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host/IP to bind to.")
    parser.add_argument("--port", type=int, default=5000, help="TCP port to listen on.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    server = TwoPersonChatServer()
    try:
        server.accept_clients(args.host, args.port)
    except KeyboardInterrupt:
        print("\nStopping chat server...")
        server.shutdown_event.set()


if __name__ == "__main__":
    main()
