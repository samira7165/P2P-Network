# p2p_node.py
# The networking part of the project (no GUI code here).
#
# In peer-to-peer (P2P) there is no central server. Every peer plays two roles
# at the same time:
#   - SERVER: it listens on a port and accepts connections from other peers.
#   - CLIENT: it can connect to other peers itself.
# Once two peers are connected, either side can send text or files.

import os          # file paths and file sizes
import socket      # TCP networking
import threading   # run several things at once (accepting, receiving, sending)
import uuid        # generate a random unique ID

import protocol    # our own message format (see protocol.py)

DOWNLOAD_FOLDER = "downloads"   # received files are saved here


class P2PNode:
    def __init__(self, name, port, log_callback, peers_changed_callback):
        self.name = name       # display name, e.g. "Alice"
        self.port = port       # port we listen on, e.g. 5000
        # Random unique ID, cut to 8 characters, e.g. "a83f21c4".
        # Names can repeat, but IDs are unique, so peers are identified by ID.
        self.peer_id = uuid.uuid4().hex[:8]

        # Functions given to us by the GUI (main.py):
        #   log(text)        -> show a line in the messages box
        #   peers_changed()  -> tell the GUI to redraw the peer list
        self.log = log_callback
        self.peers_changed = peers_changed_callback

        self.server_socket = None   # the listening socket, created in start()
        self.running = False        # True while the node is active

        # All connected peers:
        #   peer_id -> {"name", "socket", "address", "lock"}
        self.peers = {}
        # Several threads use self.peers at the same time, so this lock
        # makes sure only one thread changes it at a time.
        self.peers_lock = threading.Lock()

        # Create the downloads folder if it does not exist yet.
        os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)

    # ---------------- Server role ----------------

    def start(self):
        # AF_INET = IPv4, SOCK_STREAM = TCP.
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Lets us reuse the port right away after a restart
        # (otherwise the OS may say "address already in use" for a while).
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # "0.0.0.0" = accept connections on every network interface
        # (localhost, Wi-Fi, Ethernet...).
        self.server_socket.bind(("0.0.0.0", self.port))
        self.server_socket.listen()   # start listening for connections
        self.running = True

        # accept() blocks (waits) until someone connects. If it ran here, the
        # GUI would freeze, so it runs in a background thread.
        # daemon=True means the thread ends automatically when the program closes.
        threading.Thread(target=self.accept_loop, daemon=True).start()
        self.log(f"[SYSTEM] Peer started: {self.name} [{self.peer_id}] on port {self.port}")

    def accept_loop(self):
        # Keep accepting new peers for as long as the node runs.
        while self.running:
            try:
                # Waits here until a peer connects.
                # conn = new socket for that peer, address = (ip, port) of that peer.
                conn, address = self.server_socket.accept()
            except OSError:
                break   # the server socket was closed by stop(), so exit the loop
            # Handle each new peer in its own thread, so we can go straight back
            # to accepting more peers.
            threading.Thread(target=self.handle_incoming, args=(conn, address),
                             daemon=True).start()

    def handle_incoming(self, conn, address):
        # Someone connected to us. Handshake: they send HELLO, we reply HELLO_ACK.
        try:
            msg = protocol.recv_message(conn)   # wait for their first message
            if msg.get("type") != "hello":
                # Not a peer from our program, so reject it.
                conn.close()
                return
            # Reply with our own ID, name and port.
            protocol.send_message(conn, protocol.make_hello_ack(self.peer_id, self.name, self.port))
        except (ConnectionError, OSError, ValueError):
            # Connection broke or sent invalid JSON during the handshake.
            conn.close()
            return

        # address[1] is the peer's temporary outgoing port, which is not useful.
        # Show their LISTENING port (sent in the HELLO) instead.
        peer_address = f"{address[0]}:{msg['port']}"
        if self.add_peer(msg["peer_id"], msg["peer_name"], conn, peer_address):
            self.log(f"[SYSTEM] Connected to {msg['peer_name']}")
            # We are already in a separate thread, so we can listen for
            # this peer's messages right here.
            self.receive_loop(msg["peer_id"], conn)

    # ---------------- Client role ----------------

    def connect(self, ip, port):
        # We connect to another peer. Handshake: we send HELLO, they reply HELLO_ACK.
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)   # give up after 5 seconds if the IP/port is wrong
        try:
            sock.connect((ip, port))   # open the TCP connection
            protocol.send_message(sock, protocol.make_hello(self.peer_id, self.name, self.port))
            msg = protocol.recv_message(sock)   # wait for their HELLO_ACK
            # Remove the timeout. After the handshake we may wait a long
            # time for messages, and that is normal.
            sock.settimeout(None)
        except (OSError, ConnectionError, ValueError) as e:
            # e.g. nobody listening on that port, timeout, invalid reply
            sock.close()
            self.log(f"[ERROR] Connection failed: {e}")
            return

        if msg.get("type") != "hello_ack":
            # They answered, but not with the expected handshake reply.
            sock.close()
            self.log("[ERROR] Connection failed: no HELLO_ACK received")
            return

        if self.add_peer(msg["peer_id"], msg["peer_name"], sock, f"{ip}:{port}"):
            self.log(f"[SYSTEM] Connected to {msg['peer_name']} ({ip}:{port})")
            # Start a background thread that listens for messages from this peer.
            threading.Thread(target=self.receive_loop, args=(msg["peer_id"], sock),
                             daemon=True).start()

    # ---------------- Peer list ----------------

    def add_peer(self, peer_id, name, sock, address):
        # Returns True if the peer was added, False if it was rejected.
        if peer_id == self.peer_id:
            # The other side has OUR ID, so we connected to ourselves.
            self.log("[ERROR] You cannot connect to yourself")
            sock.close()
            return False
        with self.peers_lock:   # only one thread may change self.peers at a time
            if peer_id in self.peers:
                # Already connected, so don't keep a second connection.
                self.log(f"[SYSTEM] Already connected to {name}")
                sock.close()
                return False
            # Each peer gets its own lock so two sends to the same peer
            # can't mix their bytes together (see send_text / send_file).
            self.peers[peer_id] = {"name": name, "socket": sock, "address": address,
                                   "lock": threading.Lock()}
        self.peers_changed()   # tell the GUI to refresh the list
        return True

    def remove_peer(self, peer_id):
        # pop() removes the peer and returns it, or None if it was not there.
        with self.peers_lock:
            peer = self.peers.pop(peer_id, None)
        if peer:
            try:
                # shutdown() tells the other peer we are leaving (sends FIN),
                # so their recv() returns empty and they notice the disconnect.
                peer["socket"].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass   # socket was already broken, so ignore
            try:
                peer["socket"].close()   # free the socket
            except OSError:
                pass
            self.peers_changed()   # tell the GUI to refresh the list
        return peer

    def get_peers(self):
        # Returns a simple list of (peer_id, name, address) for the GUI.
        # A copy is made inside the lock, so the GUI never reads a half-changed dict.
        with self.peers_lock:
            return [(pid, p["name"], p["address"]) for pid, p in self.peers.items()]

    # ---------------- Receiving ----------------

    def receive_loop(self, peer_id, sock):
        # Runs in its own thread for each connected peer.
        # Waits for messages from that peer forever, until it disconnects.
        try:
            while self.running:
                msg = protocol.recv_message(sock)   # waits for the next message
                if msg["type"] == "text":
                    self.log(f"{msg['sender_name']} -> You: {msg['message']}")
                elif msg["type"] == "file":
                    # File metadata arrived. The raw file bytes come next.
                    self.receive_file(sock, msg)
        except (ConnectionError, OSError, ValueError, KeyError):
            pass   # peer disconnected or sent bad data, so stop listening

        # Loop ended, so clean up this peer.
        peer = self.remove_peer(peer_id)
        # Only report it if the peer was still in the list and we are not
        # shutting down ourselves.
        if peer and self.running:
            self.log(f"[SYSTEM] {peer['name']} disconnected")

    def receive_file(self, sock, msg):
        # basename() removes any folder part, e.g. "../../evil.exe" -> "evil.exe".
        # That way a peer can't write files outside the downloads folder.
        filename = os.path.basename(msg["filename"])
        filesize = int(msg["filesize"])
        if filesize < 0 or not filename:
            raise ValueError("Invalid file metadata")

        path = os.path.join(DOWNLOAD_FOLDER, filename)
        # Don't overwrite an existing file: photo.jpg -> photo(1).jpg -> photo(2).jpg ...
        base, ext = os.path.splitext(filename)   # "photo.jpg" -> ("photo", ".jpg")
        count = 1
        while os.path.exists(path):
            path = os.path.join(DOWNLOAD_FOLDER, f"{base}({count}){ext}")
            count += 1

        self.log(f"[SYSTEM] Receiving {filename} ({filesize} bytes) from {msg['sender_name']}...")
        remaining = filesize   # bytes still to read
        with open(path, "wb") as f:   # "wb" = write binary (works for any file type)
            # Read EXACTLY filesize bytes. If we read more, we would eat the
            # start of the next message. If we read less, the file would be incomplete.
            while remaining > 0:
                # Never ask for more than what is left of this file.
                chunk = sock.recv(min(protocol.CHUNK_SIZE, remaining))
                if not chunk:
                    raise ConnectionError("Peer disconnected during file transfer")
                f.write(chunk)
                remaining -= len(chunk)

        self.log(f"{msg['sender_name']} -> You: File received: {filename} (saved to {path})")

    # ---------------- Sending ----------------

    def send_text(self, peer_id, text):
        peer = self.peers.get(peer_id)
        if not peer:
            self.log("[ERROR] Peer is not connected")
            return
        try:
            # Hold this peer's lock so a text can't get mixed into the
            # middle of a file that another thread is sending.
            with peer["lock"]:
                protocol.send_message(peer["socket"],
                                      protocol.make_text(self.peer_id, self.name, text))
            self.log(f"You -> {peer['name']}: {text}")
        except OSError as e:
            # Sending failed, so the connection is broken. Remove the peer.
            self.log(f"[ERROR] Could not send message: {e}")
            self.remove_peer(peer_id)

    def send_file(self, peer_id, filepath):
        peer = self.peers.get(peer_id)
        if not peer:
            self.log("[ERROR] Peer is not connected")
            return
        if not os.path.isfile(filepath):
            self.log(f"[ERROR] File does not exist: {filepath}")
            return

        filename = os.path.basename(filepath)   # only the name, without the folder
        filesize = os.path.getsize(filepath)    # size in bytes
        self.log(f"[SYSTEM] Sending {filename} ({filesize} bytes) to {peer['name']}...")
        try:
            # Hold the lock for the WHOLE transfer, so nothing else gets
            # sent to this peer in between the metadata and the file bytes.
            with peer["lock"]:
                # 1) Send the metadata (name + size) as a normal framed message.
                protocol.send_message(peer["socket"], protocol.make_file(
                    self.peer_id, self.name, filename, filesize))
                # 2) Send the raw file bytes in 64 KB chunks, so even a huge
                #    file never has to fit in memory all at once.
                with open(filepath, "rb") as f:   # "rb" = read binary
                    while True:
                        chunk = f.read(protocol.CHUNK_SIZE)
                        if not chunk:
                            break   # reached the end of the file
                        peer["socket"].sendall(chunk)
            self.log(f"You -> {peer['name']}: File sent: {filename}")
        except OSError as e:
            self.log(f"[ERROR] File transfer failed: {e}")
            self.remove_peer(peer_id)

    # ---------------- Stop ----------------

    def stop(self):
        self.running = False   # tells all loops to stop
        if self.server_socket:
            try:
                # Wakes up the blocked accept() in accept_loop, so it can exit.
                self.server_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.server_socket.close()
        # Disconnect from every peer. get_peers() returns a copy, so it is
        # safe to remove peers while looping over it.
        for peer_id, _, _ in self.get_peers():
            self.remove_peer(peer_id)
        self.log("[SYSTEM] Peer stopped")
