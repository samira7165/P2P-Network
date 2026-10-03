# main.py
# The window (GUI) of the program, built with Tkinter.
# It only handles buttons and text boxes. All networking is in p2p_node.py.
#
# Run with:  python main.py

import ipaddress                  # checks whether an IP address is valid
import queue                      # thread-safe queue (see "Thread-safe events")
import threading                  # run network work without freezing the window
import tkinter as tk              # the GUI library
from tkinter import filedialog    # the "choose a file" popup

from p2p_node import P2PNode


class App:
    def __init__(self, root):
        self.root = root   # the main window
        self.root.title("UAP P2P Network")
        self.root.geometry("850x620")   # window size: width x height in pixels

        self.node = None     # the P2PNode. None until "Start Peer" is clicked
        # peer_id for each row of the peer listbox, in the same order.
        # The listbox only stores text, so this tells us which peer row N is.
        self.peer_ids = []

        # Tkinter is NOT thread-safe: only the main thread may touch widgets.
        # Network threads put events into this queue instead, and the main
        # thread reads the queue every 100 ms (see process_events).
        self.events = queue.Queue()

        self.build_ui()
        # Run on_close when the window's X button is clicked.
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        # Check the event queue for the first time after 100 ms.
        self.root.after(100, self.process_events)

    # ---------------- Layout ----------------

    def build_ui(self):
        # Note: pack() places widgets. side="left" puts them in a row,
        # fill="x" stretches horizontally, padx/pady add spacing.

        # --- "My Peer" box: our name, port, Start/Stop buttons ---
        frame_me = tk.LabelFrame(self.root, text="My Peer", padx=5, pady=5)
        frame_me.pack(fill="x", padx=8, pady=4)

        tk.Label(frame_me, text="Name:").pack(side="left")
        self.name_entry = tk.Entry(frame_me, width=15)
        self.name_entry.insert(0, "Alice")   # default value
        self.name_entry.pack(side="left", padx=4)

        tk.Label(frame_me, text="Port:").pack(side="left")
        self.port_entry = tk.Entry(frame_me, width=8)
        self.port_entry.insert(0, "5000")    # default value
        self.port_entry.pack(side="left", padx=4)

        # command= is the function that runs when the button is clicked.
        self.start_button = tk.Button(frame_me, text="Start Peer", command=self.start_peer)
        self.start_button.pack(side="left", padx=4)
        # Stop is disabled until the peer has been started.
        self.stop_button = tk.Button(frame_me, text="Stop", command=self.stop_peer,
                                     state="disabled")
        self.stop_button.pack(side="left", padx=4)

        # Shows our name, ID and port once started.
        self.info_label = tk.Label(frame_me, text="Not started")
        self.info_label.pack(side="left", padx=10)

        # --- "Connect to Another Peer" box: IP + port + Connect button ---
        frame_conn = tk.LabelFrame(self.root, text="Connect to Another Peer", padx=5, pady=5)
        frame_conn.pack(fill="x", padx=8, pady=4)

        tk.Label(frame_conn, text="IP:").pack(side="left")
        self.ip_entry = tk.Entry(frame_conn, width=15)
        self.ip_entry.insert(0, "127.0.0.1")   # 127.0.0.1 = this same computer
        self.ip_entry.pack(side="left", padx=4)

        tk.Label(frame_conn, text="Port:").pack(side="left")
        self.remote_port_entry = tk.Entry(frame_conn, width=8)
        self.remote_port_entry.insert(0, "5001")
        self.remote_port_entry.pack(side="left", padx=4)

        tk.Button(frame_conn, text="Connect", command=self.connect_peer).pack(side="left", padx=4)

        # --- Middle area: peer list on the left, message log on the right ---
        middle = tk.Frame(self.root)
        # expand=True lets this area grow when the window is resized.
        middle.pack(fill="both", expand=True, padx=8, pady=4)

        frame_peers = tk.LabelFrame(middle, text="Connected Peers")
        frame_peers.pack(side="left", fill="y")
        # exportselection=False keeps the selected peer selected even when
        # you click into another text box.
        self.peer_list = tk.Listbox(frame_peers, width=32, height=12, exportselection=False)
        self.peer_list.pack(fill="both", expand=True, padx=4, pady=4)

        frame_log = tk.LabelFrame(middle, text="Messages / Events")
        frame_log.pack(side="left", fill="both", expand=True, padx=(8, 0))
        # state="disabled" makes it read-only for the user.
        # wrap="word" breaks long lines between words.
        self.log_box = tk.Text(frame_log, height=12, state="disabled", wrap="word")
        # Connect a scrollbar to the text box (both directions).
        scroll = tk.Scrollbar(frame_log, command=self.log_box.yview)
        self.log_box.config(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.log_box.pack(fill="both", expand=True, padx=4, pady=4)

        # --- "Send Text" box ---
        frame_text = tk.LabelFrame(self.root, text="Send Text", padx=5, pady=5)
        frame_text.pack(fill="x", padx=8, pady=4)
        self.message_entry = tk.Entry(frame_text)
        self.message_entry.pack(side="left", fill="x", expand=True)
        # Pressing Enter in the text box also sends the message.
        self.message_entry.bind("<Return>", lambda event: self.send_text())
        tk.Button(frame_text, text="Send", command=self.send_text).pack(side="left", padx=4)

        # --- "Send File" box ---
        frame_file = tk.LabelFrame(self.root, text="Send File", padx=5, pady=5)
        frame_file.pack(fill="x", padx=8, pady=4)
        tk.Label(frame_file, text="Text, image, audio, video, PDF, ZIP, etc.").pack(side="left")
        tk.Button(frame_file, text="Choose File & Send",
                  command=self.send_file).pack(side="right")

    # ---------------- Thread-safe events ----------------

    def log(self, text):
        # Called from network threads. It does NOT touch the GUI,
        # it only puts the text in the queue.
        self.events.put(("log", text))

    def peers_changed(self):
        # Called from network threads when a peer connects or disconnects.
        self.events.put(("peers", None))

    def process_events(self):
        # Runs on the main thread every 100 ms. Handles everything in the queue.
        while not self.events.empty():
            kind, data = self.events.get()
            if kind == "log":
                # Temporarily allow editing, add the line, scroll to the
                # bottom, then make it read-only again.
                self.log_box.config(state="normal")
                self.log_box.insert("end", data + "\n")
                self.log_box.see("end")
                self.log_box.config(state="disabled")
            elif kind == "peers":
                self.refresh_peer_list()
        # Schedule the next check in 100 ms (this keeps repeating).
        self.root.after(100, self.process_events)

    # ---------------- Helpers ----------------

    def refresh_peer_list(self):
        # Clear the listbox and fill it again from the node's current peers.
        self.peer_list.delete(0, "end")
        self.peer_ids = []
        if self.node:
            for peer_id, name, address in self.node.get_peers():
                self.peer_list.insert("end", f"{name} [{peer_id}] {address}")
                self.peer_ids.append(peer_id)   # row N of the listbox = peer_ids[N]

    def read_port(self, entry):
        # Returns the port as an int, or None if it is invalid.
        try:
            port = int(entry.get())
            if 1 <= port <= 65535:   # valid TCP port range
                return port
        except ValueError:
            pass   # not a number
        self.log(f"[ERROR] Invalid port: {entry.get()}")
        return None

    def selected_peer(self):
        # Returns the peer_id of the selected row, or None if nothing is selected.
        selection = self.peer_list.curselection()   # tuple of selected row numbers
        if not selection:
            self.log("[ERROR] Please select a peer from the Connected Peers list first")
            return None
        return self.peer_ids[selection[0]]

    # ---------------- Button actions ----------------

    def start_peer(self):
        name = self.name_entry.get().strip()   # strip() removes extra spaces
        if not name:
            self.log("[ERROR] Please enter a peer name")
            return
        port = self.read_port(self.port_entry)
        if port is None:
            return

        # Create the node and give it our log/peers_changed functions,
        # so it can report back to the GUI.
        self.node = P2PNode(name, port, self.log, self.peers_changed)
        try:
            self.node.start()
        except OSError as e:
            # Usually the port is already used by another program or peer.
            self.log(f"[ERROR] Could not start peer on port {port}: {e}")
            self.node = None
            return

        # Update the GUI: show our info and lock the name/port fields.
        self.info_label.config(text=f"{name} | ID: {self.node.peer_id} | Port: {port}")
        self.start_button.config(state="disabled")
        self.stop_button.config(state="normal")
        self.name_entry.config(state="disabled")
        self.port_entry.config(state="disabled")

    def stop_peer(self):
        if self.node:
            self.node.stop()   # close all connections
            self.node = None
        # Reset the GUI back to the "not started" state.
        self.refresh_peer_list()
        self.info_label.config(text="Not started")
        self.start_button.config(state="normal")
        self.stop_button.config(state="disabled")
        self.name_entry.config(state="normal")
        self.port_entry.config(state="normal")

    def connect_peer(self):
        if not self.node:
            self.log("[ERROR] Start your peer first")
            return
        ip = self.ip_entry.get().strip()
        if ip == "localhost":
            ip = "127.0.0.1"   # ipaddress does not accept the word "localhost"
        try:
            ipaddress.ip_address(ip)   # raises ValueError if the IP is not valid
        except ValueError:
            self.log(f"[ERROR] Invalid IP address: {ip}")
            return
        port = self.read_port(self.remote_port_entry)
        if port is None:
            return
        # Connecting can take up to 5 seconds (timeout), so do it in a
        # thread to keep the window responsive.
        threading.Thread(target=self.node.connect, args=(ip, port), daemon=True).start()

    def send_text(self):
        if not self.node:
            self.log("[ERROR] Start your peer first")
            return
        peer_id = self.selected_peer()
        if peer_id is None:
            return
        text = self.message_entry.get().strip()
        if not text:
            return   # don't send empty messages
        self.message_entry.delete(0, "end")   # clear the text box
        # Send in a thread so a slow network never freezes the window.
        threading.Thread(target=self.node.send_text, args=(peer_id, text), daemon=True).start()

    def send_file(self):
        if not self.node:
            self.log("[ERROR] Start your peer first")
            return
        peer_id = self.selected_peer()
        if peer_id is None:
            return
        # Open the "choose a file" popup. It returns "" if the user cancels.
        filepath = filedialog.askopenfilename(title="Choose a file to send")
        if not filepath:
            return
        # Big files take time, so send in a thread to keep the window responsive.
        threading.Thread(target=self.node.send_file, args=(peer_id, filepath),
                         daemon=True).start()

    def on_close(self):
        # The window's X button was clicked. Stop the node cleanly, then close.
        if self.node:
            self.node.stop()
        self.root.destroy()


# This part runs only when you start this file directly (python main.py),
# not when another file imports it.
if __name__ == "__main__":
    root = tk.Tk()     # create the main window
    App(root)          # build our app inside it
    root.mainloop()    # show the window and wait for clicks (runs until closed)
