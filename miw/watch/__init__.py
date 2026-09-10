"""The event path: detect that a vendor moved, and resolve it to the taught slice."""
from miw.watch.signal import Signal, Watermark, row_set_hash
from miw.watch.poll import poll_all, resolve_signal

__all__ = ["Signal", "Watermark", "row_set_hash", "poll_all", "resolve_signal"]
