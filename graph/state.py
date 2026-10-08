# graph/state.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Shared State Definition
#
# WHAT CHANGED IN PHASE 3:
#   - Added conversation_history to preserve multi-turn context across turns.
#
# WHY THIS EXISTS:
#   In Phase 0, each component (Listener, Brain, Executor) was a separate
#   object that passed data through function return values. That works for a
#   linear pipeline, but falls apart the moment you need branching logic,
#   sleep/wake transitions, or a dashboard that needs to read Nida's current
#   state.
#
#   LangGraph's answer is a single typed State object that every node reads
#   from and writes to. Think of it like Redux store for an agent — one source
#   of truth that the entire graph can inspect at any point.
#
# HOW LANGGRAPH USES THIS:
#   1. The graph is initialised with an instance of NidaState.
#   2. Each node receives the FULL current state as input.
#   3. Each node returns a PARTIAL dict of only the fields it changed.
#   4. LangGraph merges those changes back into the state automatically.
#   5. The edge conditions (what runs next) read from this same state.
#
#   This means nodes are pure functions: (state) → partial_update.
#   No side effects on shared mutable objects. Easy to test, easy to debug.
#
# WHAT CHANGED IN PHASE 2:
#   - intent_type now uses "tool" instead of "action"
#   - Added tool_name and tool_args for structured tool dispatch
#   - Renamed action_stdout/stderr → action_output/error (tool-agnostic)
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal, Optional


# ── Sleep/Wake constants ──────────────────────────────────────────────────────
SLEEP_AFTER_SECONDS = 30 * 60          # 30 minutes of inactivity → sleep
# During dev/testing you probably want this much shorter:
# SLEEP_AFTER_SECONDS = 60             # 1 minute — uncomment for testing


# ── Agent modes ───────────────────────────────────────────────────────────────
AgentMode = Literal["awake", "sleeping", "shutting_down"]


@dataclass
class NidaState:
    """
    The single source of truth for the entire Nida agent graph.

    Every node in the graph receives a copy of this state, modifies
    only the fields it cares about, and returns those changes as a dict.

    Fields are grouped by which node primarily owns them:
      - listen_*    → populated by the Listen node
      - llm_*       → populated by the Reason node
      - tool_*      → populated by the Reason node (parsed from TOOL: line)
      - action_*    → populated by the Act node
      - mode        → managed by the graph's edge logic
      - timestamps  → updated by multiple nodes
    """

    # ── Conversation history (Phase 3) ───────────────────────────────────────
    # conversation_history holds the recent verbatim turns (recency window).
    # conversation_summary holds a compressed summary of older turns.
    # Together they implement Summary Buffer Memory.
    # Set by: Respond node each cycle.
    # Read by: Reason node (passed to Brain.think).
    conversation_history: list = field(default_factory=list)
    conversation_summary: str  = ""

    # ── Persona (Phase 3B) ───────────────────────────────────────────────────
    # Defines what system prompt / personality Nida uses.
    # Set by: ModeTool
    # Read by: Reason node, passed to Brain
    persona: str = "assistant"

    # ── What the user just said ───────────────────────────────────────────────
    # Set by: Listen node
    # Read by: Reason node
    user_text: str = ""

    # ── What the LLM decided to do ────────────────────────────────────────────
    # Set by: Reason node
    # Read by: Act node, Respond node
    intent_type: Literal["tool", "answer", "error", ""] = ""
    intent_content: str = ""        # answer text (when intent_type == "answer")
    intent_raw: str = ""            # raw LLM output — for logging/debug

    # ── Tool dispatch (Phase 2 & 3C) ──────────────────────────────────────────
    # Set by: Reason node (parsed from TOOL: line)
    # Read by: Act node
    # Each item: {"name": "tool_name", "args": {"arg1": "val"}}
    tools_to_run: list[dict] = field(default_factory=list)

    # ── What the tool produced ────────────────────────────────────────────────
    # Set by: Act node
    # Read by: Respond node
    action_success: bool = False
    action_output: str = ""          # unified output from any tool
    action_error: str = ""           # unified error from any tool
    action_blocked: bool = False    # True if safety gate stopped execution

    # ── Response that will be spoken ─────────────────────────────────────────
    # Set by: Respond node (assembles the final string to speak)
    response_text: str = ""

    # ── Sleep / Wake management ───────────────────────────────────────────────
    mode: AgentMode = "awake"

    # Unix timestamp of the last time the user interacted with Nida.
    # Initialised to now so Nida doesn't immediately sleep on startup.
    last_interaction_time: float = field(default_factory=time.time)

    # Set to True by the dashboard's wake button or the Ctrl+Alt+N hotkey.
    # The Listen node checks this on each iteration and resets it.
    wake_requested: bool = False

    # Set to True by the dashboard's sleep button or voice "go to sleep".
    sleep_requested: bool = False

    # ── Session metadata ──────────────────────────────────────────────────────
    turn_count: int = 0             # increments every completed listen→respond cycle
    error_count: int = 0            # consecutive errors — used for backoff

    # ── Pending SafeTerminal confirmation ─────────────────────────────────────
    # When SafeTerminal is active, the Act node sets these and waits.
    # The dashboard writes `confirmed = True/False` to unblock the node.
    # (Used in Part 3 — defined here so state schema is stable from day one.)
    pending_command: str = ""       # command waiting for user confirmation
    confirmed: Optional[bool] = None  # None = waiting, True = approved, False = denied

    # ── Convenience helpers ───────────────────────────────────────────────────

    def seconds_since_interaction(self) -> float:
        return time.time() - self.last_interaction_time

    def should_sleep(self) -> bool:
        """Returns True if inactivity threshold has been crossed."""
        return (
            self.mode == "awake"
            and not self.sleep_requested       # explicit request handled separately
            and self.seconds_since_interaction() > SLEEP_AFTER_SECONDS
        )

    def touch(self):
        """Call this whenever the user interacts — resets the sleep clock."""
        self.last_interaction_time = time.time()

    def reset_turn(self):
        """Clear per-turn fields so stale data doesn't bleed into next cycle."""
        self.user_text      = ""
        self.intent_type    = ""
        self.intent_content = ""
        self.intent_raw     = ""
        self.tools_to_run   = []
        self.action_success = False
        self.action_output  = ""
        self.action_error   = ""
        self.action_blocked = False
        self.response_text  = ""
        # NOTE: conversation_history is intentionally NOT reset here —
        # it must persist across turns to give the LLM context.
