"""
Torrent client implementations.

The `base.TorrentClient` Protocol defines the contract every concrete
client implements: login, add a .torrent file, list torrents, get one
by hash. Phase 1 ships only `qbittorrent.QbitClient`. Stubs for
Deluge / Transmission / rTorrent will land in a later phase if and
when they're actually needed — Hermeece YAGNIs them out of v1 because
the only torrent client OP uses is qBit.

Designing for the abstraction (rather than just calling qBit's API
inline from the rest of the code) costs ~50 lines and makes the
eventual addition of another backend a single-file drop-in. The shape
of `AddResult` and `TorrentInfo` is deliberately client-agnostic.
"""
