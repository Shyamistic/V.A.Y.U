"""Typed disabled-by-default mock bench state machine."""

from __future__ import annotations

import time
from dataclasses import dataclass

from .canonical import sha256_hex
from .contracts import BenchAck, BenchCapability, BenchCommand, BenchFault, BenchState, Provenance


def _prov() -> Provenance:
    return Provenance(label="derived", source_id="bench-mock", source_version="bench-mock-1", input_refs=["bench-mock"])


@dataclass
class MockBench:
    capability: BenchCapability
    state: BenchState

    @classmethod
    def create(cls, endpoint_id: str = "bess-a-mock") -> "MockBench":
        commands = {"charge_mw": {"min_value": 0.0, "max_value": 8.0, "ramp_mw_per_slot": 2.0, "dwell_slots": 1}, "discharge_mw": {"min_value": 0.0, "max_value": 8.0, "ramp_mw_per_slot": 2.0, "dwell_slots": 1}}
        safe_state = {"charge_mw": 0.0, "discharge_mw": 0.0, "enabled": False}
        if endpoint_id == "grid-main-mock":
            commands = {"import_mw": {"min_value": 0.0, "max_value": 75.0, "ramp_mw_per_slot": 20.0, "dwell_slots": 1}, "export_mw": {"min_value": 0.0, "max_value": 45.0, "ramp_mw_per_slot": 20.0, "dwell_slots": 1}}
            safe_state = {"import_mw": 0.0, "export_mw": 0.0, "enabled": False}
        capability = BenchCapability(endpoint_id=endpoint_id, protocol_version="bench-mock-1", clock_source="replay_clock", telemetry_signals=["power_mw", "energy_mwh"], command_capabilities=commands, safe_state=safe_state, provenance=_prov())
        digest = sha256_hex({"endpoint_id": endpoint_id, "state": "DISABLED"})
        state = BenchState(endpoint_id=endpoint_id, state="DISABLED", previous_output=dict(capability.safe_state), current_output=dict(capability.safe_state), last_sequence=0, last_slot=0, state_digest=digest, provenance=_prov())
        return cls(capability, state)

    def enable(self) -> BenchState:
        if self.state.state == "SAFE_DISCONNECTED":
            raise RuntimeError("fresh capability read and recovery are required")
        self.state = self.state.model_copy(update={"state": "READY", "previous_output": self.state.current_output})
        return self.state

    def send(self, command: BenchCommand, *, wall_clock_ack_seconds: float | None = None) -> tuple[BenchState, BenchFault | None]:
        current = self.state
        if current.state not in {"READY", "ACKED"}:
            return self._fault(command, "DISCONNECT", "endpoint is not ready")
        if command.sequence <= current.last_sequence:
            return self._fault(command, "DUPLICATE_SEQUENCE", "sequence must increase")
        if command.expected_state_digest != current.state_digest:
            return self._fault(command, "BAD_DIGEST", "command state digest does not match")
        if command.expires_at_slot <= command.issued_slot:
            return self._fault(command, "EXPIRED", "command expiry must be in the future")
        for name, bound in self.capability.command_capabilities.items():
            value = float(getattr(command, name))
            previous = float(current.current_output.get(name, 0.0))
            if not bound.min_value <= value <= bound.max_value:
                return self._fault(command, "OUT_OF_RANGE", f"{name} is outside capability")
            if abs(value - previous) > bound.ramp_mw_per_slot:
                return self._fault(command, "RAMP", f"{name} exceeds ramp")
            if current.last_sequence and command.issued_slot - current.last_slot < bound.dwell_slots and abs(value - previous) > 1e-9:
                return self._fault(command, "DWELL", f"{name} violates minimum dwell")
        self.state = current.model_copy(update={"state": "COMMAND_SENT", "previous_output": current.current_output, "current_output": {**current.current_output, **{key: getattr(command, key) for key in self.capability.command_capabilities}, "enabled": True}, "last_sequence": command.sequence, "last_slot": command.issued_slot})
        elapsed = 0.0 if wall_clock_ack_seconds is None else wall_clock_ack_seconds
        if elapsed > self.capability.ack_timeout_seconds:
            self.state, fault = self._fault(command, "ACK_TIMEOUT", "acknowledgement exceeded wall-clock timeout")
            return self.state, fault
        ack = BenchAck(command_id=command.command_id, endpoint_id=command.endpoint_id, sequence=command.sequence, status="ACKED", acknowledged_wall_seconds=elapsed, state_digest=self.state.state_digest, provenance=_prov())
        self.state = self.state.model_copy(update={"state": "ACKED", "state_digest": sha256_hex(ack)})
        return self.state, None

    def disconnect(self, slot: int) -> tuple[BenchState, BenchFault]:
        fault = BenchFault(endpoint_id=self.capability.endpoint_id, fault_code="DISCONNECT", slot_index=slot, message="transport disconnected", safe_state_applied=True, provenance=_prov())
        self.state = self.state.model_copy(update={"state": "SAFE_DISCONNECTED", "previous_output": self.state.current_output, "current_output": dict(self.capability.safe_state), "state_digest": sha256_hex(fault)})
        return self.state, fault

    def recover(self) -> BenchState:
        self.state = self.state.model_copy(update={"state": "READY", "previous_output": self.state.current_output, "last_sequence": 0, "state_digest": sha256_hex({"endpoint_id": self.capability.endpoint_id, "state": "READY"})})
        return self.state

    def _fault(self, command: BenchCommand, code: str, message: str):
        fault = BenchFault(endpoint_id=self.capability.endpoint_id, fault_code=code, slot_index=command.issued_slot, message=message, safe_state_applied=True, provenance=_prov())
        self.state = self.state.model_copy(update={"state": "SAFE_DISCONNECTED", "previous_output": self.state.current_output, "current_output": dict(self.capability.safe_state), "state_digest": sha256_hex(fault)})
        return self.state, fault
