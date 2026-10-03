# protocol.py
# This file decides HOW messages travel between peers over the network.
#
# Problem: TCP is a continuous stream of bytes. If I send "hello" and then
# "world", the other side might receive "hellowor" + "ld". TCP does not know
# where one message ends and the next one begins.
#
# Solution ("length-prefix framing"): before every message we send 4 bytes
# that say how long the message is:
#
#   [4-byte length][JSON payload]
#
# The receiver first reads the 4 bytes, learns the length, and then reads
# exactly that many bytes. That gives back one complete message.

import json     # turns Python dicts into text (JSON) and back
import struct   # packs numbers into raw bytes and unpacks them

HEADER_SIZE = 4           # the length header is always 4 bytes (max ~4 GB)
CHUNK_SIZE = 64 * 1024    # files are sent/read 64 KB at a time


def send_message(sock, message):
    # Turn the dict into a JSON string, then into bytes (UTF-8).
    data = json.dumps(message).encode("utf-8")
    # "!I" = unsigned 4-byte integer in network byte order (big-endian).
    # This is the length header that goes in front of the data.
    header = struct.pack("!I", len(data))
    # sendall() keeps sending until every byte has gone out.
    sock.sendall(header + data)


def recv_exact(sock, size):
    # recv() may return FEWER bytes than we ask for, so we keep calling it
    # in a loop until we have collected exactly `size` bytes.
    data = b""
    while len(data) < size:
        part = sock.recv(size - len(data))   # ask only for what is still missing
        if not part:
            # recv() returns empty bytes when the other side closed the connection
            raise ConnectionError("Peer disconnected")
        data += part
    return data


def recv_message(sock):
    # Step 1: read the 4-byte header.
    header = recv_exact(sock, HEADER_SIZE)
    # Step 2: convert those 4 bytes back into a number (the message length).
    length = struct.unpack("!I", header)[0]
    # Step 3: read exactly that many bytes, the JSON message itself.
    data = recv_exact(sock, length)
    # Step 4: bytes -> text -> Python dict.
    return json.loads(data.decode("utf-8"))


# ---------------- Message builders ----------------
# Each function below builds one type of message as a dict.
# The "type" field tells the receiver what kind of message it is.


def make_hello(peer_id, peer_name, port):
    # Sent first by the peer that STARTS a connection, to introduce itself.
    # `port` is the port this peer listens on, so the other side can show it.
    return {"type": "hello", "peer_id": peer_id, "peer_name": peer_name, "port": port}


def make_hello_ack(peer_id, peer_name, port):
    # The reply to HELLO. It means "I accept you, and here is who I am."
    return {"type": "hello_ack", "peer_id": peer_id, "peer_name": peer_name, "port": port}


def make_text(sender_id, sender_name, message):
    # A normal chat message.
    return {"type": "text", "sender_id": sender_id,
            "sender_name": sender_name, "message": message}


def make_file(sender_id, sender_name, filename, filesize):
    # File METADATA only (name + size). The real file bytes are sent right
    # after this message, directly on the socket, without JSON.
    return {"type": "file", "sender_id": sender_id, "sender_name": sender_name,
            "filename": filename, "filesize": filesize}
