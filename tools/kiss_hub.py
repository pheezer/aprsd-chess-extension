"""A fake RF channel for local testing: relays KISS frames between TCP clients.

Point several aprsd instances (`[kiss_tcp] host/port`) at this hub and every
data frame one of them transmits is delivered to all the others, the way a
shared radio channel would.  Nothing is ever transmitted on the air.

With `--baud`, the hub behaves like a half-duplex channel at that bit rate:
frames go out one at a time and each one is delivered only after its airtime
(preamble plus 8 bits per byte), so end-to-end timings are realistic for a
direct path with no digipeaters and no collisions.  `--record` writes each
frame's start/end time and bytes to a JSON-lines file.

    python tools/kiss_hub.py --port 8011 --baud 1200 --record frames.jsonl
"""

from __future__ import annotations

import argparse
import json
import queue
import socket
import threading
import time

FEND = 0xC0
# Spacing between frames in instant mode.  aprsd 5.x reads one frame per TCP
# read (aprsd/client/drivers/tcpkiss.py read_frame), so frames that arrive
# together get merged into one garbled packet.
MIN_GAP = 0.3
FESC = 0xDB
TFEND = 0xDC
TFESC = 0xDD


def unescape(data: bytes) -> bytes:
    out = bytearray()
    escaped = False
    for byte in data:
        if escaped:
            out.append(FEND if byte == TFEND else FESC if byte == TFESC else byte)
            escaped = False
        elif byte == FESC:
            escaped = True
        else:
            out.append(byte)
    return bytes(out)


class Hub:
    def __init__(self, verbose=False, baud=0, txdelay=0.28, record=None):
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()
        self.verbose = verbose
        self.baud = baud
        self.txdelay = txdelay
        self.record = record
        self.record_lock = threading.Lock()
        self.channel: queue.Queue = queue.Queue()
        threading.Thread(target=self._channel_loop, daemon=True).start()

    def airtime(self, ax25: bytes) -> float:
        if not self.baud:
            # Even "instant" frames go out a moment apart: aprsd's TCP KISS
            # reader treats each socket read as exactly one frame, so two
            # frames arriving together would be merged into one bad packet.
            return MIN_GAP
        # Preamble/tail, then the frame and its 2-byte FCS at `baud`.
        return self.txdelay + (len(ax25) + 2) * 8 / self.baud

    def submit(self, sender: socket.socket, frame: bytes) -> None:
        self.channel.put((sender, frame))

    def _channel_loop(self) -> None:
        while True:
            sender, frame = self.channel.get()
            ax25 = unescape(frame[2:-1])  # drop FEND, command byte, FEND
            start = time.time()
            time.sleep(self.airtime(ax25))
            end = time.time()
            self._log_frame(sender, frame, start, end)
            self.broadcast(sender, frame)

    def _log_frame(self, sender, frame: bytes, start: float, end: float) -> None:
        if self.verbose:
            print(f"frame {len(frame)} bytes {end - start:.3f}s", flush=True)
        if self.record:
            entry = {
                "start": start,
                "end": end,
                "ax25": unescape(frame[2:-1]).hex(),
            }
            with self.record_lock, open(self.record, "a", encoding="utf-8") as fp:
                fp.write(json.dumps(entry) + "\n")

    def broadcast(self, sender: socket.socket, frame: bytes) -> None:
        with self.lock:
            targets = [c for c in self.clients if c is not sender]
        for client in targets:
            try:
                client.sendall(frame)
            except OSError:
                self.drop(client)

    def drop(self, client: socket.socket) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
        try:
            client.close()
        except OSError:
            pass

    def serve(self, client: socket.socket, address) -> None:
        with self.lock:
            self.clients.append(client)
        if self.verbose:
            print(f"connect {address}", flush=True)
        buffer = bytearray()
        try:
            while True:
                data = client.recv(4096)
                if not data:
                    break
                buffer.extend(data)
                # Forward each complete FEND-delimited frame.
                while True:
                    start = buffer.find(FEND)
                    if start < 0:
                        buffer.clear()
                        break
                    end = buffer.find(FEND, start + 1)
                    if end < 0:
                        del buffer[:start]
                        break
                    frame = bytes(buffer[start : end + 1])
                    del buffer[:end]
                    if len(frame) > 3:  # skip empty FEND FEND pairs
                        self.submit(client, frame)
        except OSError:
            pass
        finally:
            if self.verbose:
                print(f"disconnect {address}", flush=True)
            self.drop(client)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8011)
    parser.add_argument(
        "--baud", type=int, default=0, help="Simulate a channel at this bit rate."
    )
    parser.add_argument(
        "--txdelay",
        type=float,
        default=0.28,
        help="Seconds of preamble/tail per frame when simulating (default 0.28, "
        "the overhead direwolf's gen_packets puts on each frame).",
    )
    parser.add_argument("--record", help="Append each frame to this JSON-lines file.")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    hub = Hub(
        verbose=args.verbose, baud=args.baud, txdelay=args.txdelay, record=args.record
    )
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((args.host, args.port))
    server.listen()
    mode = f"{args.baud} baud channel" if args.baud else "instant relay"
    print(f"KISS hub ({mode}) listening on {args.host}:{args.port}", flush=True)
    while True:
        client, address = server.accept()
        threading.Thread(target=hub.serve, args=(client, address), daemon=True).start()


if __name__ == "__main__":
    main()
