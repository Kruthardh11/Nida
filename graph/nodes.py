# graph/nodes.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — All Graph Nodes
#
# WHY NODES INSTEAD OF METHODS:
#   In Phase 0, the agent was one class with methods (_handle_action, etc).
#   The problem: methods share 'self', so adding new behaviour means touching
#   the same class and risking regressions.
#
#   LangGraph nodes are independent functions with a clear contract:
#     Input:  NidaState  (read whatever you need)
#     Output: dict       (return ONLY the fields you changed)
#
#   This means you can add a new tool in Part 2 without touching any of the
#   nodes defined here. Loose coupling by design.
#
# WHAT CHANGED IN PHASE 2:
#   - reason_node now maps tool_name/tool_args from NidaResponse
#   - act_node dispatches through ToolRegistry instead of calling Executor
#   - _build_response uses action_output/action_error (tool-agnostic)
#   - intent_type "action" is now "tool"
#
# NODE OVERVIEW:
#   listen_node   — captures microphone, transcribes with Whisper
#   reason_node   — sends text to Ollama, parses TOOL/ANSWER
#   act_node      — dispatches to the correct tool via ToolRegistry
#   respond_node  — assembles response_text, calls TTS
#   sleep_node    — kills mic stream, logs sleep time
#   wake_node     — restores mic stream, announces return
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from graph.state import NidaState

if TYPE_CHECKING:
    # Avoid circular imports — these are only needed for type hints
    from core.listener import Listener
    from core.brain import Brain
    from core.voice import Voice
    from tools.registry import ToolRegistry

logger = logging.getLogger("nida.nodes")


# ─────────────────────────────────────────────────────────────────────────────
# Node factory
#
# Each `make_*_node` function closes over the component instances (Listener,
# Brain, etc.) and returns a plain function with signature (state) → dict.
#
# WHY A FACTORY INSTEAD OF A CLASS:
#   LangGraph expects callables with signature (state) → dict.
#   A factory lets us inject dependencies (Listener, Brain...) while keeping
#   the node itself a simple function. No inheritance, no magic.
# ─────────────────────────────────────────────────────────────────────────────

def make_listen_node(listener: "Listener"):
    """
    LISTEN NODE
    ───────────
    Responsibility: Capture audio from the mic and return transcribed text.

    Sleep check: Before blocking on the mic, the node checks two things:
      1. Was a wake requested externally? (dashboard button / hotkey)
      2. Has the inactivity timer expired?  → if yes, signal sleep

    Returns fields: user_text, last_interaction_time, mode, wake_requested
    """
    # Phrases that trigger voice-based sleep
    SLEEP_TRIGGERS = ["sleep nida", "go to sleep", "nida sleep", "take a rest"]

    def listen_node(state: NidaState) -> dict:
        from core.signals import signals
        logger.debug("Node: LISTEN")

        # ── Handle explicit wake request (from hotkey/dashboard) ──────────────
        if signals.wake_requested:
            logger.info("Wake requested — resuming from sleep.")
            signals.wake_requested = False
            return {
                "mode": "awake",
                "wake_requested": False,
                "last_interaction_time": time.time()
            }

        # ── Check if inactivity timeout has elapsed ───────────────────────────
        if state.should_sleep():
            idle_mins = state.seconds_since_interaction() / 60
            logger.info(f"Inactivity timeout ({idle_mins:.1f} min). Going to sleep.")
            return {"mode": "sleeping"}

        # ── Check for explicit sleep request (hotkey/dashboard) ───────────────
        if signals.sleep_requested:
            logger.info("Sleep requested explicitly.")
            signals.sleep_requested = False
            return {"mode": "sleeping", "sleep_requested": False}

        # ── Normal path: block until the user speaks ──────────────────────────
        text = listener.listen()

        if not text:
            return {"user_text": ""}

        logger.info(f"Heard: '{text}'")

        # ── Intercept sleep trigger phrases before sending to LLM ────────────
        if any(phrase in text for phrase in SLEEP_TRIGGERS):
            logger.info(f"Sleep phrase detected: '{text}'")
            return {"mode": "sleeping", "sleep_requested": False, "user_text": ""}

        return {
            "user_text": text,
            "last_interaction_time": time.time(),
        }

    return listen_node


def make_reason_node(brain: "Brain"):
    """
    REASON NODE
    ───────────
    Responsibility: Send user_text to Ollama and parse the response.

    Why this is separate from listen_node:
      The LLM call is the most latency-variable step (~200ms–3s).
      Keeping it isolated means we can later add a cache layer here,
      or swap in a different model, without touching anything else.

    Returns fields: intent_type, intent_content, intent_raw, tool_name, tool_args
    """
    def reason_node(state: NidaState) -> dict:
        logger.debug(f"Node: REASON | input='{state.user_text}'")

        response = brain.think(
            state.user_text,
            history=state.conversation_history,
            summary=state.conversation_summary,
            persona=state.persona,
        )

        return {
            "intent_type":    response.type,
            "intent_content": response.content,
            "intent_raw":     response.raw,
            "tools_to_run":   response.tools,
        }

    return reason_node


def make_act_node(registry: "ToolRegistry"):
    """
    ACT NODE
    ────────
    Responsibility: Dispatch to the correct tool via the ToolRegistry.

    Phase 2 change:
      Previously this called executor.run(command) directly.
      Now it looks up the tool by name in the registry and calls tool.run(args).
      If the tool isn't found, it returns an error — no crash, no fallback.

    Returns fields: action_success, action_output, action_error, action_blocked
    """
    def act_node(state: NidaState) -> dict:
        names = [t.get("name") for t in state.tools_to_run]
        logger.debug(f"Node: ACT | intent_type={state.intent_type}, tools={names}")

        # Only execute if the LLM classified this as a tool call
        if state.intent_type != "tool" or not state.tools_to_run:
            return {}   # nothing to do — respond_node will handle the answer

        all_success = True
        all_outputs = []
        all_errors = []
        final_blocked = False
        final_state_updates = {}

        for tool_dict in state.tools_to_run:
            t_name = tool_dict.get("name")
            t_args = tool_dict.get("args", {})
            
            tool = registry.get(t_name)
            if tool is None:
                logger.error(f"Unknown tool: '{t_name}'")
                all_success = False
                all_errors.append(f"Unknown tool: '{t_name}'")
                continue

            # Execute the tool
            result = tool.run(t_args)
            
            if not result.success:
                all_success = False
            if result.error:
                all_errors.append(result.error)
            if result.output:
                all_outputs.append(result.output)
            if result.blocked:
                final_blocked = True
                
            if hasattr(result, "state_updates") and result.state_updates:
                final_state_updates.update(result.state_updates)

        update_dict = {
            "action_success": all_success,
            "action_output":  "\n".join(all_outputs),
            "action_error":   "\n".join(all_errors),
            "action_blocked": final_blocked,
        }
        
        if final_state_updates:
            update_dict.update(final_state_updates)

        return update_dict

    return act_node


def make_respond_node(voice: "Voice", brain: "Brain"):
    """
    RESPOND NODE
    ────────────
    Responsibility: Assemble the final response string and speak it.

    This node is the only place that calls voice.speak(). All other nodes
    are silent. This makes it easy to suppress TTS (e.g. for tests) by
    swapping this one node.

    Returns fields: response_text, turn_count, conversation_history, conversation_summary
    """
    def respond_node(state: NidaState) -> dict:
        logger.debug("Node: RESPOND")

        text = _build_response(state)
        logger.info(f"Speaking: '{text}'")

        voice.speak(text)

        # ── Append this turn to conversation history ──────────────────────────
        # We store what the user said and what Nida responded so the
        # LLM has full conversational context on the next turn.
        new_history = list(state.conversation_history)  # copy to avoid mutation
        new_summary = state.conversation_summary

        if state.user_text:
            new_history.append({"role": "user", "content": state.user_text})
            
            # The LLM's system prompt strictly demands "TOOL:" or "ANSWER:".
            # If we don't format the history exactly like this, the LLM hallucinates
            # and forgets the rules on subsequent turns.
            if state.intent_type == "tool":
                # Provide the raw LLM output (the TOOL: text) + the result of the tool
                assistant_memory = f"{state.intent_raw.strip()}\n[Tool Output: {text}]"
                new_history.append({"role": "assistant", "content": assistant_memory})
            else:
                new_history.append({"role": "assistant", "content": f"ANSWER: {text}"})

        # ── Summary Buffer Memory: compress when history grows too long ────────
        # When raw history exceeds SUMMARY_TRIGGER, fold all-but-RECENCY_WINDOW
        # turns into the rolling summary and keep only the recency window raw.
        if len(new_history) > brain.SUMMARY_TRIGGER:
            to_summarise = new_history[:-brain.RECENCY_WINDOW]
            new_summary  = brain.summarize_history(to_summarise, new_summary)
            new_history  = new_history[-brain.RECENCY_WINDOW:]
            logger.info(
                f"Memory compacted: {len(to_summarise)} msgs summarised, "
                f"{len(new_history)} raw msgs kept."
            )

        return {
            "response_text":         text,
            "turn_count":            state.turn_count + 1,
            "conversation_history":  new_history,
            "conversation_summary":  new_summary,
        }

    return respond_node


def make_sleep_node(voice: "Voice", listener: "Listener"):
    """
    SLEEP NODE
    ──────────
    Responsibility: Announce sleep, then loop on a lightweight wake-word
    detector until either a wake phrase is spoken or wake_requested is set
    by the Ctrl+Alt+N / Ctrl+Alt+S hotkey.

    Wake phrases: 'hey nida', 'nida wake up', 'wake up nida', 'nida let's go'
    Sleep phrases trigger: handled in listen_node before reaching here.

    Returns fields: mode, wake_requested
    """
    WAKE_PHRASES = ["hey nida", "nida wake up", "wake up nida", "nida let's go",
                    "nida lets go", "wake up", "hey nida wake up"]

    def sleep_node(state: NidaState) -> dict:
        from core.signals import signals
        logger.info("Node: SLEEP — entering sleep mode.")
        voice.speak_sync("Going to sleep. Say 'hey Nida' or press Ctrl+Alt+N to wake me up.")

        # Lightweight loop — keeps mic open just enough to catch wake phrases.
        # CPU cost is near zero during silence.
        while True:
            # Hotkey sets wake_requested directly on the signals object
            if signals.wake_requested:
                logger.info("Wake hotkey triggered during sleep.")
                signals.wake_requested = False
                break

            detected = listener.listen_for_wakeword(WAKE_PHRASES)
            if detected:
                logger.info("Wake phrase detected — resuming.")
                break

        return {"mode": "awake", "wake_requested": False}

    return sleep_node


def make_wake_node(voice: "Voice", listener: "Listener"):
    """
    WAKE NODE
    ─────────
    Responsibility: Announce Nida is back and reset the interaction clock.

    Returns fields: mode, wake_requested, last_interaction_time
    """
    def wake_node(state: NidaState) -> dict:
        logger.info("Node: WAKE — resuming from sleep.")
        voice.speak_sync("Hey! I'm back. What do you need?")
        return {
            "mode": "awake",
            "wake_requested": False,
            "last_interaction_time": time.time(),
        }

    return wake_node


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_response(state: NidaState) -> str:
    """
    Assembles the string Nida will speak based on what happened this turn.
    Centralised here so respond_node stays clean.
    """
    # ── Safety block ──────────────────────────────────────────────────────────
    if state.action_blocked:
        return "Command blocked for safety."

    # ── Direct answer from LLM ────────────────────────────────────────────────
    if state.intent_type == "answer":
        return state.intent_content

    # ── Tool result ───────────────────────────────────────────────────────────
    if state.intent_type == "tool":
        if state.action_success:
            if state.action_output:
                # Speak short output up to ~3-4 paragraphs; print longer output to terminal
                if len(state.action_output) <= 800:
                    return state.action_output
                else:
                    print(f"\n{'─'*40}\n{state.action_output}\n{'─'*40}")
                    return "Done. Output is in the terminal."
            return "Done."
        else:
            err = state.action_error or "Unknown error."
            return f"That didn't work. {err[:100]}"

    # ── Error / unknown ───────────────────────────────────────────────────────
    if state.intent_type == "error":
        return state.intent_content or "Something went wrong."

    return "I didn't catch that."
