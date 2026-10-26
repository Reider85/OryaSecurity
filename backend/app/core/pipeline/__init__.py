from __future__ import annotations

from .fast_path import FastPath, FastPathResult, fast_path
from .suspicion_tagger import SuspicionTagger, SuspicionResult, suspicion_tagger
from .slow_path import SlowPath, SlowPathResult, get_slow_path, set_slow_path

__all__ = [
    "FastPath",
    "FastPathResult", 
    "fast_path",
    "SuspicionTagger",
    "SuspicionResult",
    "suspicion_tagger",
    "SlowPath",
    "SlowPathResult",
    "get_slow_path",
    "set_slow_path",
]