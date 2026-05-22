from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Optional


class Verdict(str, Enum):
    OK = "OK"
    DNS_BLOCK = "DNS_BLOCK"
    TCP_RESET = "TCP_RESET"
    TLS_BLOCK = "TLS_BLOCK"
    HTTP_STUB = "HTTP_STUB"
    TIMEOUT = "TIMEOUT"
    DOWN = "DOWN"
    UNKNOWN = "UNKNOWN"


class Confidence(str, Enum):
    """How confident we are in a verdict.

    HIGH   - two independent signals confirm the diagnosis (e.g. DNS poisoning
             confirmed by DoH returning a different IP, or an explicit HTTP 451,
             or a known stub-page marker in the body)
    MEDIUM - a known censorship pattern matches, but a single signal can't
             rule out a server-side issue or a flaky network (e.g. TCP RST,
             TLS handshake aborted on ClientHello)
    LOW    - symptom is ambiguous (timeout, generic failure) and could be
             caused by anything from DPI to a flaky uplink
    """
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


BLOCKED_VERDICTS: frozenset[Verdict] = frozenset({
    Verdict.DNS_BLOCK,
    Verdict.TCP_RESET,
    Verdict.TLS_BLOCK,
    Verdict.HTTP_STUB,
    Verdict.TIMEOUT,
})


@dataclass
class CheckResult:
    name: str
    url: str

    verdict: Verdict = Verdict.UNKNOWN
    confidence: Confidence = Confidence.LOW
    notes: list[str] = field(default_factory=list)

    sys_ip: Optional[str] = None
    doh_ip: Optional[str] = None
    sys_ips: list[str] = field(default_factory=list)
    doh_ips: list[str] = field(default_factory=list)
    dns_mismatch: bool = False
    dns_error: Optional[str] = None

    tcp_ok: bool = False
    tcp_time_ms: Optional[float] = None
    tcp_error: Optional[str] = None

    tls_ok: bool = False
    tls_time_ms: Optional[float] = None
    tls_cert_cn: Optional[str] = None
    tls_error: Optional[str] = None

    status_code: Optional[int] = None
    plt_ms: Optional[float] = None
    http_error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        d["confidence"] = self.confidence.value
        return d