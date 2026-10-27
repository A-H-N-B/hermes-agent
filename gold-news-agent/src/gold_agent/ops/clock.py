"""System clock offset (C4.1). Latency numbers are only as good as the clock.

In order: a fresh offset file (Docker: the container has no chrony, so a host timer writes the
offset there, scripts/clock_offset.sh), `chronyc` (native Linux), then one SNTP query to
`watchdog.ntp_server` (Windows/macOS or Linux without chrony). Positive = our clock is ahead.
"""

from __future__ import annotations

import asyncio
import shutil
import struct
import time
from pathlib import Path

OFFSET_FILE_MAX_AGE = 600.0  # seconds; the host timer writes every minute
NTP_EPOCH_DELTA = 2_208_988_800  # seconds from 1900-01-01 (NTP) to 1970-01-01 (Unix)
NTP_TIMEOUT_SECONDS = 2.0
NTP_MIN_INTERVAL_SECONDS = 900.0  # pool.ntp.org etiquette: a checker asks rarely
NTP_RETRY_SECONDS = 60.0  # after a failed query


def parse_tracking(csv_line: str) -> float | None:
    """`chronyc -c tracking`: field 5 is the system time offset in seconds."""
    fields = csv_line.strip().split(",")
    if len(fields) < 5:
        return None
    try:
        return float(fields[4])
    except ValueError:
        return None


async def chrony_offset(executable: str | None = None) -> float | None:
    chronyc = executable or shutil.which("chronyc")
    if chronyc is None:
        return None
    process = await asyncio.create_subprocess_exec(
        chronyc,
        "-c",
        "tracking",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=5)
    except TimeoutError:
        process.kill()
        return None
    return parse_tracking(stdout.decode(errors="replace")) if process.returncode == 0 else None


def file_offset(path: Path, *, max_age_seconds: float = OFFSET_FILE_MAX_AGE) -> float | None:
    """The offset the host timer wrote, unless it stopped writing: then nobody can tell."""
    try:
        if time.time() - path.stat().st_mtime > max_age_seconds:
            return None
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _to_ntp(unix: float) -> int:
    return int((unix + NTP_EPOCH_DELTA) * 2**32)


def _from_ntp(stamp: int) -> float:
    return stamp / 2**32 - NTP_EPOCH_DELTA


def sntp_request(sent: float) -> bytes:
    """Client request (version 4, mode 3) whose transmit time the server echoes back."""
    packet = bytearray(48)
    packet[0] = 0x23
    struct.pack_into("!Q", packet, 40, _to_ntp(sent))
    return bytes(packet)


def parse_sntp(reply: bytes, sent: float, received: float) -> float | None:
    """Offset from one exchange (RFC 4330): the reply must be a synchronised server answer
    to our request, else None."""
    if len(reply) < 48:
        return None
    leap, mode, stratum = reply[0] >> 6, reply[0] & 0x7, reply[1]
    if mode != 4 or stratum == 0 or leap == 3:
        return None  # not a server reply, kiss-of-death, or the server is unsynchronised
    originate, server_received, server_sent = struct.unpack_from("!QQQ", reply, 24)
    if originate != _to_ntp(sent):
        return None  # an answer to some other request
    server_ahead = ((_from_ntp(server_received) - sent) + (_from_ntp(server_sent) - received)) / 2
    return -server_ahead


class _SntpReply(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.reply: asyncio.Future[tuple[bytes, float]] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: object) -> None:
        if not self.reply.done():
            self.reply.set_result((data, time.time()))

    def error_received(self, exc: Exception) -> None:
        if not self.reply.done():
            self.reply.set_exception(exc)


async def sntp_offset(server: str, port: int = 123) -> float | None:
    loop = asyncio.get_running_loop()
    try:
        transport, protocol = await loop.create_datagram_endpoint(
            _SntpReply, remote_addr=(server, port)
        )
    except OSError:
        return None
    try:
        sent = time.time()
        transport.sendto(sntp_request(sent))
        async with asyncio.timeout(NTP_TIMEOUT_SECONDS):
            reply, received = await protocol.reply
    except (OSError, TimeoutError):
        return None
    finally:
        transport.close()
    return parse_sntp(reply, sent, received)


class ClockOffset:
    """The watchdog's offset reader: file and chronyc on every check, the NTP server at most
    every NTP_MIN_INTERVAL_SECONDS (the last answer is reused in between)."""

    def __init__(self, ntp_server: str | None) -> None:
        self._server = ntp_server
        self._asked_at: float | None = None
        self._answer: float | None = None

    async def __call__(self, offset_file: str | None) -> float | None:
        """Offset in seconds (positive = our clock is ahead), or None when nobody can tell."""
        if offset_file:
            value = await asyncio.to_thread(file_offset, Path(offset_file))
            if value is not None:
                return value
        value = await chrony_offset()
        if value is not None or not self._server:
            return value
        now = time.monotonic()
        wait = NTP_MIN_INTERVAL_SECONDS if self._answer is not None else NTP_RETRY_SECONDS
        if self._asked_at is None or now - self._asked_at >= wait:
            self._asked_at, self._answer = now, await sntp_offset(self._server)
        return self._answer
