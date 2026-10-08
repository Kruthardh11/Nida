# graph/edges.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Edge Conditions (Routing Logic)
#
# WHY EDGES ARE SEPARATE FROM NODES:
#   Nodes do work. Edges make decisions about what runs next.
#   Keeping them separate means you can read the entire routing logic of
#   the agent in one file, without wading through implementation details.
#
# HOW LANGGRAPH EDGES WORK:
#   A "conditional edge" is a function that receives the current state and
#   returns a string — the name of the next node to run.
#
#   Example:
#     graph.add_conditional_edges("listen", after_listen)
#     # after_listen(state) returns "reason", "sleep", or "listen"
#     # LangGraph routes to whichever node name is returned.
#
# THE ROUTING MAP (Phase 2 — TOOL: protocol):
#
#   ┌─────────────────────────────────────────────────────────────────┐
#   │                                                                 │
#   │   [START] → listen ──→ (sleeping?) → sleep → listen (loop)    │
#   │                  ↓                                              │
#   │               (awake, has text)                                 │
#   │                  ↓                                              │
#   │              reason ──→ (tool?) → act ──→ respond ──→ listen  │
#   │                  ↓                                              │
#   │            (answer, skip act)                                   │
#   │                  ↓                                              │
#   │              respond ──→ listen (loop)                          │
#   │                                                                 │
#   │   sleep ─────────────────────────────→ listen (polls for wake) │
#   │                                                                 │
#   └─────────────────────────────────────────────────────────────────┘
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
from graph.state import NidaState

logger = logging.getLogger("nida.edges")

# Exit phrases handled at the edge level so they short-circuit the whole graph
EXIT_PHRASES = frozenset({
    "exit", "quit", "stop", "goodbye", "bye",
    "shutdown nida", "shut down", "see you later"
})


def after_listen(state: NidaState) -> str:
    """
    Called after the listen node completes.

    Decision tree:
      shutting_down → END
      sleeping      → sleep      (enter sleep node to announce + pause mic)
      empty text    → listen     (nothing heard, loop back)
      exit phrase   → END
      else          → reason     (we have text, think about it)
    """
    if state.mode == "shutting_down":
        logger.info("Edge: listen → END (shutdown)")
        return "END"

    if state.mode == "sleeping":
        logger.info("Edge: listen → sleep")
        return "sleep"

    if not state.user_text.strip():
        logger.debug("Edge: listen → listen (empty)")
        return "listen"

    # Check for exit intent before sending to LLM (saves a round-trip)
    if any(phrase in state.user_text.lower() for phrase in EXIT_PHRASES):
        logger.info(f"Edge: listen → END (exit phrase: '{state.user_text}')")
        return "END"

    logger.debug("Edge: listen → reason")
    return "reason"


def after_reason(state: NidaState) -> str:
    """
    Called after the reason node completes.

    Decision tree:
      tool   → act         (dispatch to registered tool)
      answer → respond     (LLM answered directly, skip act)
      error  → respond     (LLM error, respond_node handles messaging)
    """
    if state.intent_type == "tool":
        tool_names = [t.get("name", "unknown") for t in state.tools_to_run]
        logger.debug(f"Edge: reason → act (tools={tool_names})")
        return "act"

    # answer or error — skip execution, go straight to respond
    logger.debug(f"Edge: reason → respond (intent_type={state.intent_type})")
    return "respond"


def after_act(state: NidaState) -> str:
    """
    Called after the act node completes.
    Always routes to respond — act just populates the action_* fields.
    """
    logger.debug("Edge: act → respond")
    return "respond"


def after_respond(state: NidaState) -> str:
    """
    Called after the respond node completes.
    Always loops back to listen for the next command.

    This is where we reset per-turn state so stale data doesn't
    bleed into the next cycle.
    """
    # NOTE: We can't call state.reset_turn() here because LangGraph
    # expects edge functions to be read-only. The reset is done by
    # returning a partial update dict from a cleanup step, OR we rely
    # on each node overwriting the fields it cares about.
    logger.debug("Edge: respond → listen")
    return "listen"


def after_sleep(state: NidaState) -> str:
    """
    Called after the sleep node completes.

    The sleep node pauses the mic. The graph then loops back to listen,
    which will poll state.wake_requested and state.mode on each iteration.
    When the dashboard sets wake_requested=True, listen_node handles it.
    """
    logger.debug("Edge: sleep → listen (polling for wake)")
    return "listen"
