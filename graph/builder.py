# graph/builder.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Graph Assembly
#
# WHY A SEPARATE BUILDER:
#   The graph wiring (add_node, add_edge, add_conditional_edges) is pure
#   plumbing — it doesn't belong in any node or in main.py.
#   Isolating it here means you can read the full graph topology in one place,
#   and swap out any node without touching anything else.
#
# WHAT CHANGED IN PHASE 2:
#   build_graph() now accepts a ToolRegistry instead of a bare Executor.
#   make_act_node(registry) dispatches to any registered tool by name.
#
# HOW TO READ THIS FILE:
#   1. build_graph() creates the StateGraph and wires all nodes + edges.
#   2. The returned graph is a compiled LangGraph runnable.
#   3. main.py calls build_graph() once and then calls graph.invoke() in a loop.
#
# LANGGRAPH MENTAL MODEL:
#   - StateGraph: the graph object. Holds the node registry + edge rules.
#   - add_node(name, fn): registers a node. fn must be (state) → dict.
#   - add_edge(a, b): unconditional: a always goes to b.
#   - add_conditional_edges(a, fn): fn(state) returns the name of next node.
#   - set_entry_point(name): which node runs first.
#   - compile(): returns a runnable. Validates the graph structure.
#   - graph.invoke(state): runs from entry point until END.
#   - graph.stream(state): same but yields state after each node (for UI).
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging

from langgraph.graph import StateGraph, END

from graph.state import NidaState
from graph.nodes import (
    make_listen_node,
    make_reason_node,
    make_act_node,
    make_respond_node,
    make_sleep_node,
)
from graph.edges import (
    after_listen,
    after_reason,
    after_act,
    after_respond,
    after_sleep,
)

logger = logging.getLogger("nida.builder")


def build_graph(listener, brain, voice, registry):
    """
    Assembles and compiles the Nida StateGraph.

    Parameters
    ----------
    listener  : core.listener.Listener
    brain     : core.brain.Brain
    voice     : core.voice.Voice
    registry  : tools.registry.ToolRegistry

    Returns
    -------
    CompiledGraph — call .invoke(NidaState()) to run it.
    """
    logger.info("Building LangGraph StateGraph...")

    # ── 1. Create the graph with our state schema ─────────────────────────────
    #
    # StateGraph(NidaState) tells LangGraph:
    #   "The state object flowing through this graph is of type NidaState.
    #    When a node returns a partial dict, merge it into NidaState."
    #
    graph = StateGraph(NidaState)

    # ── 2. Register nodes ─────────────────────────────────────────────────────
    #
    # Each make_*_node() call closes over the component instances and returns
    # a plain function (state: NidaState) → dict.
    #
    graph.add_node("listen",  make_listen_node(listener))
    graph.add_node("reason",  make_reason_node(brain))
    graph.add_node("act",     make_act_node(registry))
    graph.add_node("respond", make_respond_node(voice, brain))
    graph.add_node("sleep",   make_sleep_node(voice, listener))

    # ── 3. Set entry point ────────────────────────────────────────────────────
    #
    # Graph always starts at the listen node.
    #
    graph.set_entry_point("listen")

    # ── 4. Wire conditional edges ─────────────────────────────────────────────
    #
    # add_conditional_edges(source, router_fn, mapping)
    #   source     : node that just finished
    #   router_fn  : (state) → string key
    #   mapping    : dict of key → node name (or END)
    #
    # The mapping makes the routing explicit and validates at compile time
    # that all possible return values are accounted for.
    #
    graph.add_conditional_edges(
        "listen",
        after_listen,
        {
            "reason": "reason",
            "sleep":  "sleep",
            "listen": "listen",     # loop (nothing heard)
            "END":    END,
        }
    )

    graph.add_conditional_edges(
        "reason",
        after_reason,
        {
            "act":     "act",
            "respond": "respond",
        }
    )

    graph.add_conditional_edges(
        "act",
        after_act,
        {
            "respond": "respond",
        }
    )

    graph.add_conditional_edges(
        "respond",
        after_respond,
        {
            "listen": "listen",
        }
    )

    graph.add_conditional_edges(
        "sleep",
        after_sleep,
        {
            "listen": "listen",
        }
    )


    # ── 5. Compile ────────────────────────────────────────────────────────────
    #
    # compile() validates the graph topology:
    #   - Every node is reachable from the entry point
    #   - Every conditional edge mapping is exhaustive
    #   - No dangling nodes
    # It returns a CompiledGraph (a LangChain Runnable).
    #
    compiled = graph.compile()
    logger.info("Graph compiled successfully.")

    # Log the topology for debugging
    logger.debug(f"Nodes: {list(graph.nodes)}")

    return compiled
