#!/usr/bin/env python3
"""
lasercube_sim.py — a fake LaserCube on the network, so the whole output path
can be exercised with zero photons.

The point of this is arithmetic: you get a few hours with the real projector,
and every protocol bug you find there is an hour you don't get back. Discovery,
throttling, frame-drop policy, the watchdog, reconnect and teardown can all be
driven to completion against this instead.

    python scripts/lasercube_sim.py                  # behave
    python scripts/lasercube_sim.py --stall-after 5  # stop responding at 5 s
    python scripts/lasercube_sim.py --drop 0.2       # lose 20% of datagrams
    python scripts/lasercube_sim.py --refuse-enable  # never enable output
    python scripts/lasercube_sim.py --tiny-buffer    # constant backpressure

Then, in another terminal:

    python laserx3.py --output lasercube --lasercube-ip 127.0.0.1

It answers on the real ports (45457 command, 45458 data), so it also serves as
a check that the client's discovery and framing are right before you point the
client at hardware. Run it on the same machine and use --lasercube-ip
127.0.0.1; it does not answer broadcast discovery from another host.

It reports what it receives, including anything it could not parse, because a
silent simulator that accepts garbage teaches you nothing.
"""

import argparse
import random
import socket
import struct
import sys
import threading
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])

from lasercube_output import (  # noqa: E402
    CMD_GET_FULL_INFO, CMD_GET_RINGBUFFER_EMPTY_SAMPLE_COUNT,
    CMD_SAMPLE_DATA, CMD_SET_OUTPUT, CMD_ENABLE_BUFFER_SIZE_RESPONSE_ON_DATA,
    CMD_SET_RATE, CMD_CLEAR_RINGBUFFER, CMD_SET_DAC_BUFFER_THRESHOLD,
    CMD_PORT, DATA_PORT,
)

BUFFER_SIZE = 6000


class FakeCube:
    def __init__(self, args):
        self.args = args
        self.started = time.monotonic()
        self.output_on = False
        self.dac_rate = 30000
        self.buffer_free = BUFFER_SIZE if not args.tiny_buffer else 400
        self.buffer_size = BUFFER_SIZE if not args.tiny_buffer else 400
        self.points_in = 0
        self.frames_in = 0
        self.datagrams = 0
        self.bad = 0
        self.last_frame_num = None
        self.last_point = None
        self.lock = threading.Lock()

    # ---- helpers ----

    def stalled(self):
        return (self.args.stall_after is not None and
                time.monotonic() - self.started > self.args.stall_after)

    def full_info(self):
        """64-byte info response, byte-for-byte per the real layout.

        Built independently of the client's parser so it actually tests it —
        if this mirrored parse_full_info(), a shared offset error would pass.
        """
        buf = bytearray(64)
        buf[0] = CMD_GET_FULL_INFO
        buf[1] = 0                                   # status OK
        buf[2] = 0                                   # payload version
        buf[3] = 2                                   # fw major
        buf[4] = 7                                   # fw minor
        flags = 0
        if self.output_on:
            flags |= 0x01
        if not self.args.interlock_open:
            flags |= 0x02                            # interlock engaged
        if self.args.temperature >= 38:
            flags |= 0x04                            # temperature warning
        if self.args.temperature >= 41:
            flags |= 0x08                            # over temperature
        flags |= (self.args.packet_errors & 0x0F) << 4
        buf[5] = flags
        struct.pack_into("<I", buf, 10, self.dac_rate)
        struct.pack_into("<I", buf, 14, 35000)       # max dac rate (Ultra)
        struct.pack_into("<H", buf, 19, self.buffer_free)
        struct.pack_into("<H", buf, 21, self.buffer_size)
        buf[23] = 255 if self.args.mains else 87     # 255 = on mains
        struct.pack_into("<b", buf, 24, self.args.temperature)
        buf[25] = 1                                  # 1 = ethernet
        buf[26:32] = bytes.fromhex("0102030405a6")   # serial
        buf[32:36] = bytes([127, 0, 0, 1])           # reported ip
        buf[37] = 10                                 # model
        name = b"LaserCube Ultra (sim)"
        buf[38:38 + len(name)] = name
        return bytes(buf)

    # ---- the two sockets ----

    def serve_cmd(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", CMD_PORT))
        print(f"[sim] command port {CMD_PORT} listening")
        while True:
            msg, addr = s.recvfrom(2048)
            if self.stalled():
                continue
            if not msg:
                continue
            op = msg[0]
            if op == CMD_GET_FULL_INFO:
                s.sendto(self.full_info(), addr)
            elif op == CMD_GET_RINGBUFFER_EMPTY_SAMPLE_COUNT:
                with self.lock:
                    free = self.buffer_free
                s.sendto(struct.pack("<BBH", op, 0, free), addr)
            elif op == CMD_SET_OUTPUT:
                want = bool(msg[1]) if len(msg) > 1 else False
                if want and self.args.refuse_enable:
                    print("[sim] REFUSING enable-output (--refuse-enable)")
                else:
                    if want != self.output_on:
                        print(f"[sim] output {'ENABLED' if want else 'disabled'}")
                    self.output_on = want
            elif op == CMD_SET_RATE:
                if len(msg) >= 5:
                    self.dac_rate = struct.unpack_from("<I", msg, 1)[0]
                    print(f"[sim] DAC rate set to {self.dac_rate} pps")
                else:
                    self.bad += 1
                    print(f"[sim] BAD set-rate: {len(msg)} bytes, expected 5")
            elif op in (CMD_ENABLE_BUFFER_SIZE_RESPONSE_ON_DATA,
                        CMD_CLEAR_RINGBUFFER, CMD_SET_DAC_BUFFER_THRESHOLD):
                pass
            else:
                self.bad += 1
                print(f"[sim] unknown command 0x{op:02x} ({len(msg)} bytes)")

    def serve_data(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", DATA_PORT))
        print(f"[sim] data port {DATA_PORT} listening")
        while True:
            msg, _ = s.recvfrom(65535)
            if self.stalled():
                continue
            if self.args.drop and random.random() < self.args.drop:
                continue
            self.datagrams += 1
            if len(msg) < 4 or msg[0] != CMD_SAMPLE_DATA:
                self.bad += 1
                print(f"[sim] BAD datagram: {msg[:8].hex()} ({len(msg)} bytes)")
                continue
            body = msg[4:]
            if len(body) % 10:
                self.bad += 1
                print(f"[sim] BAD point payload: {len(body)} bytes "
                      "is not a multiple of 10 — wrong point size?")
                continue
            n = len(body) // 10
            frame_num = msg[3]
            if frame_num != self.last_frame_num:
                self.last_frame_num = frame_num
                self.frames_in += 1
            with self.lock:
                self.points_in += n
                self.buffer_free = max(0, self.buffer_free - n)
            if n:
                self.last_point = struct.unpack_from("<HHHHH", body, 0)

    def drain(self):
        """The scanner consuming points, so buffer_free recovers."""
        while True:
            time.sleep(0.02)
            with self.lock:
                self.buffer_free = min(self.buffer_size,
                                       self.buffer_free + int(30000 * 0.02))

    def report(self):
        last_dg = 0
        while True:
            time.sleep(2.0)
            rate = (self.datagrams - last_dg) / 2.0
            last_dg = self.datagrams
            state = "STALLED" if self.stalled() else (
                "OUTPUT ON" if self.output_on else "output off")
            pt = self.last_point
            ptxt = ""
            if pt:
                ptxt = (f" last=(x{pt[0]} y{pt[1]} "
                        f"r{pt[2]} g{pt[3]} b{pt[4]})")
                if max(pt) > 4095:
                    ptxt += "  <-- VALUE >4095, wrong scaling or field order"
            print(f"[sim] {state} | {self.datagrams} dg ({rate:.0f}/s) "
                  f"{self.frames_in} frames {self.points_in} pts "
                  f"free={self.buffer_free} bad={self.bad}{ptxt}")


def main():
    p = argparse.ArgumentParser(description="Fake LaserCube for dry runs")
    p.add_argument("--stall-after", type=float, default=None,
                   help="stop responding after N seconds (tests the watchdog)")
    p.add_argument("--drop", type=float, default=0.0,
                   help="drop this fraction of data datagrams, 0..1")
    p.add_argument("--refuse-enable", action="store_true",
                   help="never accept enable-output")
    p.add_argument("--tiny-buffer", action="store_true",
                   help="tiny ring buffer, for constant backpressure")
    p.add_argument("--temperature", type=int, default=28,
                   help="reported °C (>=38 warns, >=41 reports over-temp)")
    p.add_argument("--interlock-open", action="store_true",
                   help="report the interlock as not engaged")
    p.add_argument("--mains", action="store_true",
                   help="report mains power (battery byte 255) not a %%")
    p.add_argument("--packet-errors", type=int, default=0,
                   help="report N packet errors (0-15)")
    args = p.parse_args()

    cube = FakeCube(args)
    for fn in (cube.serve_cmd, cube.serve_data, cube.drain, cube.report):
        threading.Thread(target=fn, daemon=True).start()
    print("[sim] fake LaserCube up. NO PHOTONS. Ctrl-C to stop.")
    if args.stall_after:
        print(f"[sim] will go silent after {args.stall_after}s")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[sim] bye")


if __name__ == "__main__":
    main()
