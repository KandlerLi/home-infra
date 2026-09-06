"""Aggregate callback: a per-role breakdown alongside the normal PLAY RECAP.

The built-in PLAY RECAP only ever aggregates by host -- with ~11 roles
applied in one playbook run, "changed=10" alone doesn't say *which*
role(s) actually changed something. This walks every task result as it
comes in, groups it by the role that owns the task, and prints a second
"ROLE RECAP" banner at the end in the same shape as the normal one.

Tasks outside any role (e.g. bare tasks in a play) are grouped under
"(no role)" rather than dropped, so the totals still add up.
"""
from __future__ import annotations

from collections import defaultdict

from ansible.plugins.callback import CallbackBase

DOCUMENTATION = r"""
    name: role_recap
    type: aggregate
    short_description: Print a per-role PLAY RECAP-style summary
    description:
      - Groups task results by owning role and prints a "ROLE RECAP"
        banner after the normal PLAY RECAP, showing ok/changed/failed/
        skipped/unreachable/ignored counts per role.
    requirements:
      - enable in ansible.cfg (this callback is not loaded by default)
"""

_NO_ROLE = "(no role)"


class CallbackModule(CallbackBase):
    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = "aggregate"
    CALLBACK_NAME = "role_recap"
    CALLBACK_NEEDS_ENABLEMENT = True

    def __init__(self):
        super().__init__()
        self._counts: dict[str, dict[str, int]] = defaultdict(
            lambda: defaultdict(int)
        )

    @staticmethod
    def _role_name(result) -> str:
        role = getattr(result._task, "_role", None)
        return role.get_name() if role else _NO_ROLE

    def _record(self, result, key: str) -> None:
        self._counts[self._role_name(result)][key] += 1

    def v2_runner_on_ok(self, result) -> None:
        self._record(result, "changed" if result._result.get("changed") else "ok")

    def v2_runner_on_failed(self, result, ignore_errors: bool = False) -> None:
        self._record(result, "ignored" if ignore_errors else "failed")

    def v2_runner_on_unreachable(self, result) -> None:
        self._record(result, "unreachable")

    def v2_runner_on_skipped(self, result) -> None:
        self._record(result, "skipped")

    def v2_runner_item_on_ok(self, result) -> None:
        self.v2_runner_on_ok(result)

    def v2_runner_item_on_failed(self, result) -> None:
        self._record(result, "failed")

    def v2_runner_item_on_skipped(self, result) -> None:
        self._record(result, "skipped")

    def v2_playbook_on_stats(self, stats) -> None:
        if not self._counts:
            return

        self._display.banner("ROLE RECAP")
        name_width = max(len(name) for name in self._counts) + 2
        for role_name in sorted(self._counts):
            c = self._counts[role_name]
            line = (
                "{name:<{width}}: ok={ok:<4} changed={changed:<4} "
                "unreachable={unreachable:<4} failed={failed:<4} "
                "skipped={skipped:<4} ignored={ignored:<4}"
            ).format(
                name=role_name,
                width=name_width,
                ok=c.get("ok", 0),
                changed=c.get("changed", 0),
                unreachable=c.get("unreachable", 0),
                failed=c.get("failed", 0),
                skipped=c.get("skipped", 0),
                ignored=c.get("ignored", 0),
            )
            self._display.display(line)
