"""
tools/mock_engine.py — M2.3.2 mock ECU engine simulator for KWP testing.

Simulates a running 5-cylinder 20v turbo engine with realistic sensor values,
broadcasting over a simple TCP socket in KWPBridge state-dict format.

Usage:
    python tools/mock_engine.py [--port 50266] [--variant 551B] [--scenario cruise]

The mock produces KWPBridge-compatible state dicts so UrROM's KWP overlay
can be tested without a real ECU or KWPBridge installation.

Scenarios:
    idle      — warm idle, 850 RPM, light load, stoich lambda
    cruise    — 3000 RPM, 40% load, part-throttle, stoich
    wot       — 5000 RPM, 100% load, rich (lambda 0.85), boost
    warm_up   — cold start to warm idle, ECT rising from 20°C to 90°C
    knock     — cruise with periodic knock retard events
    cycle     — 120s loop through all scenarios

Outputs a KWPBridge-compatible JSON state dict at 10 Hz via TCP.
"""

import argparse
import json
import math
import random
import socket
import time
import threading
from dataclasses import dataclass, field
from typing import Optional


# ── Engine state ──────────────────────────────────────────────────────────────

@dataclass
class EngineState:
    """Instantaneous engine sensor values."""
    rpm:       float = 850.0
    load:      float = 20.0     # raw 0-255 (KWP group 3 cell2, ×25 = kPa equiv)
    ect:       float = 85.0     # °C coolant temperature
    iat:       float = 25.0     # °C intake air temp
    lambda_:   float = 1.0      # wideband lambda (1.0 = stoich)
    timing:    float = 12.0     # °BTDC ignition advance
    tps:       float = 8.0      # throttle %
    map_kpa:   float = 98.0     # manifold absolute pressure kPa
    n75_dc:    float = 0.0      # N75 wastegate duty %
    vss:       float = 0.0      # km/h vehicle speed
    # Derived knock state
    knock_count: int = 0
    retard_deg: float = 0.0     # active knock retard

    def to_kwp_state(self, part_number: str = "895907551B") -> dict:
        """Encode as a KWPBridge-compatible state dict."""
        # Group 1: RPM, ECT, lambda, ignition timing
        # Group 3: RPM, load, TPS, IAT
        # Group 6: N75 DC, N75 req, MAP kPa, MAP req (prjmod)

        def rpm_raw(rpm):    return int(rpm / 40)
        def ect_raw(ect):    return int(ect + 70)
        def lam_raw(lam):    return int(lam * 128)
        def ign_raw(deg):    return int((deg + 8.2186) / 0.6491)
        def load_raw(load):  return int(load)
        def tps_raw(tps):    return int(tps / 0.416)
        def iat_raw(iat):    return int(iat + 70)
        def kpa_raw(kpa):    return int(kpa / 300 * 255)

        return {
            "ecu_id": {
                "part_number": part_number,
                "variant": "M2.3.2",
            },
            "connected": True,
            "groups": {
                "1": {
                    "cells": [
                        rpm_raw(self.rpm),
                        ect_raw(self.ect),
                        lam_raw(self.lambda_),
                        ign_raw(self.timing - self.retard_deg),
                    ]
                },
                "3": {
                    "cells": [
                        rpm_raw(self.rpm),
                        load_raw(self.load),
                        tps_raw(self.tps),
                        iat_raw(self.iat),
                    ]
                },
                "6": {
                    "cells": [
                        int(self.n75_dc * 2.55),
                        int(self.n75_dc * 2.55),
                        kpa_raw(self.map_kpa),
                        kpa_raw(self.map_kpa),
                    ]
                },
            },
            "timestamp": time.time(),
        }


# ── Scenario engines ──────────────────────────────────────────────────────────

class Scenario:
    """Base class — subclass and implement step()."""
    name = "base"
    duration_s = 30.0

    def step(self, t: float, dt: float, state: EngineState) -> None:
        """Update state in-place. t = seconds since scenario start."""
        pass

    def add_noise(self, state: EngineState) -> None:
        """Add small realistic sensor noise."""
        state.rpm     += random.gauss(0, 15)
        state.load    += random.gauss(0, 1)
        state.lambda_ += random.gauss(0, 0.01)
        state.ect     += random.gauss(0, 0.1)
        state.timing  += random.gauss(0, 0.3)
        # Clamp
        state.rpm     = max(400, state.rpm)
        state.load    = max(0, min(255, state.load))
        state.lambda_ = max(0.5, min(2.0, state.lambda_))


class IdleScenario(Scenario):
    name = "idle"
    duration_s = 30.0

    def step(self, t, dt, state):
        state.rpm      = 850 + 30 * math.sin(t * 0.3)
        state.load     = 20 + 5 * math.sin(t * 0.2)
        state.tps      = 4.0 + 2 * math.sin(t * 0.15)
        state.lambda_  = 1.0 + 0.02 * math.sin(t * 1.1)
        state.timing   = 12.0
        state.map_kpa  = 45 + 3 * math.sin(t * 0.4)
        state.n75_dc   = 0.0
        state.vss      = 0.0
        self.add_noise(state)


class CruiseScenario(Scenario):
    name = "cruise"
    duration_s = 40.0

    def step(self, t, dt, state):
        state.rpm      = 3000 + 200 * math.sin(t * 0.15)
        state.load     = 60 + 15 * math.sin(t * 0.12)
        state.tps      = 25 + 5 * math.sin(t * 0.1)
        state.lambda_  = 1.0 + 0.015 * math.sin(t * 2.3)
        state.timing   = 28 + 3 * math.sin(t * 0.2)
        state.map_kpa  = 120 + 10 * math.sin(t * 0.18)
        state.n75_dc   = 35 + 5 * math.sin(t * 0.3)
        state.vss      = 80 + 5 * math.sin(t * 0.08)
        self.add_noise(state)


class WOTScenario(Scenario):
    name = "wot"
    duration_s = 20.0

    def step(self, t, dt, state):
        # RPM building from 2000 to 6500
        progress = min(t / 12.0, 1.0)
        state.rpm      = 2000 + 4500 * progress + 100 * math.sin(t * 2)
        state.load     = 200 + 40 * progress
        state.tps      = 95 + 3 * math.sin(t * 3)
        state.lambda_  = 0.88 - 0.03 * progress + 0.01 * math.sin(t * 4)
        state.timing   = 35 + 5 * progress - 3 * math.sin(t * 1.5)
        state.map_kpa  = 180 + 50 * progress
        state.n75_dc   = 60 + 15 * progress
        state.vss      = 40 + 80 * progress
        self.add_noise(state)


class WarmupScenario(Scenario):
    name = "warm_up"
    duration_s = 60.0

    def step(self, t, dt, state):
        # ECT rising from cold to warm
        state.ect      = 20 + 70 * (1 - math.exp(-t / 25))
        cold_factor    = max(0, (80 - state.ect) / 80)
        state.rpm      = 850 + 600 * cold_factor + 40 * math.sin(t * 0.4)
        state.load     = 20 + 15 * cold_factor
        state.tps      = 4 + 3 * cold_factor
        state.lambda_  = 0.92 + 0.1 * (1 - cold_factor) + 0.02 * math.sin(t * 1.2)
        state.timing   = 8 + 8 * (1 - cold_factor) + 2 * math.sin(t * 0.3)
        state.map_kpa  = 40 + 10 * (1 - cold_factor)
        state.n75_dc   = 0.0
        state.vss      = 0.0
        self.add_noise(state)


class KnockScenario(Scenario):
    name = "knock"
    duration_s = 30.0
    _next_knock = 8.0
    _retard     = 0.0

    def step(self, t, dt, state):
        # Cruise base
        state.rpm      = 4000 + 300 * math.sin(t * 0.2)
        state.load     = 140 + 20 * math.sin(t * 0.15)
        state.tps      = 60 + 10 * math.sin(t * 0.12)
        state.lambda_  = 0.98 + 0.02 * math.sin(t * 1.8)
        state.timing   = 32.0
        state.map_kpa  = 175 + 15 * math.sin(t * 0.2)
        state.n75_dc   = 55

        # Periodic knock events
        if t >= self._next_knock:
            self._retard = random.uniform(3, 8)  # knock retard
            state.knock_count += 1
            self._next_knock = t + random.uniform(4, 10)

        # Retard decays at ~2°/s
        self._retard = max(0, self._retard - 2 * dt)
        state.retard_deg = self._retard
        self.add_noise(state)


class CycleScenario(Scenario):
    """Loops through all scenarios continuously."""
    name = "cycle"
    duration_s = 9999

    def __init__(self):
        self._scenarios = [
            WarmupScenario(),
            IdleScenario(),
            CruiseScenario(),
            WOTScenario(),
            KnockScenario(),
            IdleScenario(),
        ]
        self._idx = 0
        self._t0  = 0.0

    def step(self, t, dt, state):
        s = self._scenarios[self._idx]
        local_t = t - self._t0
        s.step(local_t, dt, state)
        if local_t >= s.duration_s:
            self._idx = (self._idx + 1) % len(self._scenarios)
            self._t0  = t
            print(f"[mock_engine] → {self._scenarios[self._idx].name}")


SCENARIOS = {
    "idle":    IdleScenario,
    "cruise":  CruiseScenario,
    "wot":     WOTScenario,
    "warm_up": WarmupScenario,
    "knock":   KnockScenario,
    "cycle":   CycleScenario,
}


# ── TCP server ─────────────────────────────────────────────────────────────────

class MockECUServer:
    """
    Listens on a TCP port and streams KWPBridge state dicts at 10 Hz.
    Each connected client receives newline-delimited JSON state updates.
    """

    def __init__(self, port: int, scenario: Scenario, part_number: str,
                 rate_hz: float = 10.0):
        self._port    = port
        self._scenario = scenario
        self._pn      = part_number
        self._rate    = rate_hz
        self._state   = EngineState()
        self._clients: list[socket.socket] = []
        self._lock    = threading.Lock()
        self._running = False

    def start(self):
        self._running = True
        self._server_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._engine_thread = threading.Thread(target=self._engine_loop, daemon=True)
        self._server_thread.start()
        self._engine_thread.start()
        print(f"[mock_engine] Listening on port {self._port}")
        print(f"[mock_engine] Scenario: {self._scenario.name}  PN: {self._pn}")
        print(f"[mock_engine] Rate: {self._rate} Hz")
        print(f"[mock_engine] Ctrl+C to stop")

    def stop(self):
        self._running = False

    def _accept_loop(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", self._port))
        srv.listen(5)
        srv.settimeout(1.0)
        while self._running:
            try:
                conn, addr = srv.accept()
                conn.settimeout(5.0)
                with self._lock:
                    self._clients.append(conn)
                print(f"[mock_engine] Client connected: {addr}")
            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    print(f"[mock_engine] Accept error: {e}")
        srv.close()

    def _engine_loop(self):
        dt   = 1.0 / self._rate
        t0   = time.time()
        tick = 0
        while self._running:
            t = time.time() - t0
            self._scenario.step(t, dt, self._state)

            state_dict = self._state.to_kwp_state(self._pn)
            payload    = (json.dumps(state_dict) + "\n").encode()

            dead = []
            with self._lock:
                for c in self._clients:
                    try:
                        c.sendall(payload)
                    except Exception:
                        dead.append(c)
                for c in dead:
                    self._clients.remove(c)
                    print("[mock_engine] Client disconnected")

            # Console status every 50 ticks
            if tick % 50 == 0:
                s = self._state
                print(f"  RPM={s.rpm:5.0f}  load={s.load:5.1f}  "
                      f"ECT={s.ect:5.1f}°C  λ={s.lambda_:.3f}  "
                      f"ign={s.timing-s.retard_deg:5.1f}°  "
                      f"MAP={s.map_kpa:5.0f}kPa  "
                      f"{'KNOCK' if s.retard_deg > 0 else ''}")
            tick += 1
            time.sleep(dt)


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="M2.3.2 mock ECU engine simulator for KWP testing")
    parser.add_argument("--port",     type=int, default=50266,
                        help="TCP port (default: 50266 = KWPBridge port)")
    parser.add_argument("--scenario", choices=list(SCENARIOS), default="cycle",
                        help="Engine scenario (default: cycle)")
    parser.add_argument("--variant",  default="551B",
                        help="ECU variant name (default: 551B)")
    parser.add_argument("--pn",       default="895907551B",
                        help="ECU part number (default: 895907551B)")
    parser.add_argument("--rate",     type=float, default=10.0,
                        help="Update rate Hz (default: 10)")
    args = parser.parse_args()

    scenario_cls = SCENARIOS[args.scenario]
    scenario     = scenario_cls()

    server = MockECUServer(
        port=args.port,
        scenario=scenario,
        part_number=args.pn,
        rate_hz=args.rate,
    )
    server.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n[mock_engine] Stopped")
        server.stop()


if __name__ == "__main__":
    main()
