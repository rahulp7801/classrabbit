"""Persistent limits for one local MyUCLA browser profile, without session data."""

from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import random
import threading
import time

from .ucla import SourceError


class SafetyHold(SourceError):
    def __init__(self, message, retry_at=0):
        super().__init__(message)
        self.retry_at = retry_at


def retry_after_seconds(value, now):
    try:
        value = str(value).strip()
        if value.isdigit():
            return int(value)
        return max(0, parsedate_to_datetime(value).timestamp() - now)
    except (TypeError, ValueError, OverflowError):
        return 0


class RequestSafety:
    """One request at a time; >=10s apart, including children and other watches.

    These are conservative application limits, not a UCLA-approved quota.
    State is saved before sending so restarting cannot erase a cooldown.
    """
    spacing = 10

    def __init__(self, path, clock=time.time, jitter=None):
        self.path = Path(path)
        self.clock = clock
        self.jitter = jitter or (lambda: random.uniform(0, 1))
        self.lock = threading.RLock()
        self.state = {"next_request_at": 0, "cooldown_until": 0,
                      "pause_reason": None, "rate_limits": 0, "failures": 0}
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text(encoding="utf-8"))
                for key in ("next_request_at", "cooldown_until"):
                    if not isinstance(state[key], (int, float)) or not math.isfinite(state[key]) or state[key] < 0:
                        raise ValueError("Invalid deadline")
                for key in ("rate_limits", "failures"):
                    if type(state[key]) is not int or state[key] < 0:
                        raise ValueError("Invalid counter")
                if state["pause_reason"] not in {None, "sign_in_required", "access_denied", "rate_limit_review", "safety_state_error"}:
                    raise ValueError("Invalid pause")
                self.state = {key: state[key] for key in self.state}
            except (OSError, ValueError, TypeError, KeyError):
                self.state["pause_reason"] = "safety_state_error"

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as exc:
            self.state["pause_reason"] = "safety_state_error"
            raise SafetyHold("Monitoring paused: cannot persist request-safety state.") from exc

    def check(self):
        with self.lock:
            reason = self.state["pause_reason"]
            if reason:
                messages = {
                    "sign_in_required": "Monitoring paused. Complete sign-in and Duo manually, then resume checks in this app.",
                    "access_denied": "Monitoring stopped after HTTP 403. Check access with UCLA before restarting reads.",
                    "rate_limit_review": "Monitoring stopped after repeated rate limiting. Review permitted access with UCLA.",
                    "safety_state_error": "Monitoring paused: request-safety state needs local repair.",
                }
                raise SafetyHold(messages[reason], self.state["cooldown_until"])
            if self.clock() < self.state["cooldown_until"]:
                raise SafetyHold("MyUCLA requests are in cooldown; no section requests will be sent.", self.state["cooldown_until"])

    def acquire(self, stop_event):
        while True:
            if stop_event.is_set():
                raise SafetyHold("The MyUCLA reader is stopping.")
            with self.lock:
                self.check()
                delay = self.state["next_request_at"] - self.clock()
                if delay <= 0:
                    self.state["next_request_at"] = self.clock() + self.spacing + self.jitter()
                    self._save()
                    return
            if stop_event.wait(min(delay, .25)):
                raise SafetyHold("The MyUCLA reader is stopping.")

    def response(self, status, retry_after=None):
        with self.lock:
            now = self.clock()
            if status in {301, 302, 303, 307, 308, 401}:
                self.state["pause_reason"] = "sign_in_required"
            elif status == 403:
                self.state["pause_reason"] = "access_denied"
            elif status == 429:
                self.state["rate_limits"] += 1
                delay = max(900, retry_after_seconds(retry_after, now))
                self.state["cooldown_until"] = max(self.state["cooldown_until"], now + delay + self.jitter())
                if self.state["rate_limits"] >= 2:
                    self.state["pause_reason"] = "rate_limit_review"
            elif status != 200:
                self.state["failures"] += 1
                delay = min(3600, 60 * 2 ** min(self.state["failures"] - 1, 6))
                delay = max(delay, retry_after_seconds(retry_after, now))
                self.state["cooldown_until"] = max(self.state["cooldown_until"], now + delay + self.jitter())
            else:
                self.state["failures"] = 0
            # Also space from response completion, so disk/network latency cannot
            # turn reservations made before sending into a burst of actual reads.
            self.state["next_request_at"] = max(self.state["next_request_at"], now + self.spacing + self.jitter())
            self._save()
            self.check()

    def pause_for_sign_in(self):
        with self.lock:
            self.state["pause_reason"] = "sign_in_required"
            self._save()
            self.check()

    def resume_sign_in(self):
        with self.lock:
            if self.state["pause_reason"] != "sign_in_required":
                raise SafetyHold("Only a sign-in pause can be resumed here. Rate/access holds remain enforced.")
            self.state["pause_reason"] = None
            self._save()

    def info(self):
        with self.lock:
            return {"minimum_request_gap_seconds": self.spacing,
                    "pause_reason": self.state["pause_reason"],
                    "cooldown_until": self.state["cooldown_until"],
                    "next_request_at": self.state["next_request_at"],
                    "rate_limit_responses": self.state["rate_limits"],
                    "university_approved_quota": False}
