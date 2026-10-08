# tests/test_parse.py
# ─────────────────────────────────────────────────────────────────────────────
# Nida — Parser Unit Tests (Phase 2)
#
# Tests Brain._parse() against all expected input formats:
#   1. TOOL: with valid JSON
#   2. ANSWER: with text
#   3. ACTION: backward compat
#   4. TOOL: with bare string (lenient fallback)
#   5. No prefix, looks like a command
#   6. No prefix, looks like an answer
#   7. ANSWER: containing a command (re-route)
#   8. TOOL: with trailing garbage after JSON
#   9. TOOL: with no args
#  10. TOOL: with unknown tool name
#
# Run:  python -m pytest tests/test_parse.py -v
#   or: python tests/test_parse.py
# ─────────────────────────────────────────────────────────────────────────────

import sys
from pathlib import Path

# Add project root to sys.path so imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.brain import Brain


class FakeBrain(Brain):
    """Brain subclass that skips Ollama connectivity check."""
    def __init__(self):
        # Skip _check_ollama() — we only need the parser
        pass


def test_all():
    brain = FakeBrain()
    passed = 0
    failed = 0
    total = 0

    def check(label, raw_input, expected_type, expected_tool="", expected_content="", expected_args=None):
        nonlocal passed, failed, total
        total += 1
        result = brain._parse(raw_input)

        errors = []
        if result.type != expected_type:
            errors.append(f"type: expected '{expected_type}', got '{result.type}'")
        if expected_tool and result.tool_name != expected_tool:
            errors.append(f"tool_name: expected '{expected_tool}', got '{result.tool_name}'")
        if expected_content and result.content != expected_content:
            errors.append(f"content: expected '{expected_content}', got '{result.content}'")
        if expected_args is not None:
            if result.tool_args != expected_args:
                errors.append(f"tool_args: expected {expected_args}, got {result.tool_args}")

        if errors:
            failed += 1
            print(f"  FAIL: {label}")
            for e in errors:
                print(f"       {e}")
            print(f"       raw: '{raw_input}'")
        else:
            passed += 1
            print(f"  PASS: {label}")

    print("\n" + "=" * 60)
    print("  NIDA PARSER TESTS (Phase 2)")
    print("=" * 60 + "\n")

    # ── 1. TOOL: with valid JSON ──────────────────────────────────────────
    check(
        "TOOL: shell with valid JSON",
        'TOOL: shell {"command": "dir"}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "dir"}
    )

    check(
        "TOOL: shell with notepad command",
        'TOOL: shell {"command": "notepad.exe"}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "notepad.exe"}
    )

    check(
        "TOOL: shell with echo %TIME%",
        'TOOL: shell {"command": "echo %TIME%"}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "echo %TIME%"}
    )

    # ── 2. ANSWER: with text ──────────────────────────────────────────────
    check(
        "ANSWER: simple text",
        "ANSWER: I'm doing great, thanks for asking.",
        expected_type="answer",
        expected_content="I'm doing great, thanks for asking."
    )

    check(
        "ANSWER: knowledge question",
        "ANSWER: Machine learning is a branch of AI.",
        expected_type="answer",
        expected_content="Machine learning is a branch of AI."
    )

    # ── 3. ACTION: backward compatibility ─────────────────────────────────
    check(
        "ACTION: backward compat -> shell tool",
        "ACTION: notepad.exe",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "notepad.exe"}
    )

    check(
        "ACTION: backward compat with dir command",
        "ACTION: dir",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "dir"}
    )

    # ── 4. TOOL: with bare string (lenient fallback) ──────────────────────
    check(
        "TOOL: shell bare string (no JSON)",
        "TOOL: shell notepad.exe",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "notepad.exe"}
    )

    check(
        "TOOL: shell bare echo command",
        "TOOL: shell echo %TIME%",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "echo %TIME%"}
    )

    # ── 5. No prefix, looks like a command ────────────────────────────────
    check(
        "No prefix, raw command -> shell fallback",
        "notepad.exe",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "notepad.exe"}
    )

    check(
        "No prefix, echo command -> shell fallback",
        "echo %TIME%",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "echo %TIME%"}
    )

    # ── 6. No prefix, looks like an answer ────────────────────────────────
    check(
        "No prefix, plain text -> answer fallback",
        "I'm doing great, how about you?",
        expected_type="answer",
        expected_content="I'm doing great, how about you?"
    )

    # ── 7. ANSWER: containing a command (re-route to shell) ───────────────
    check(
        "ANSWER: with command -> re-route to shell",
        "ANSWER: echo %TIME%",
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "echo %TIME%"}
    )

    # ── 8. TOOL: with trailing garbage after JSON ─────────────────────────
    check(
        "TOOL: JSON with trailing text",
        'TOOL: shell {"command": "dir"} Here is the command to list files.',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "dir"}
    )

    # ── 9. TOOL: with no args ─────────────────────────────────────────────
    check(
        "TOOL: with no arguments",
        "TOOL: shell",
        expected_type="tool",
        expected_tool="shell",
        expected_args={}
    )

    # ── 10. TOOL: with unknown tool name ──────────────────────────────────
    check(
        "TOOL: unknown tool (parsed correctly, act_node handles error)",
        'TOOL: weather {"city": "New York"}',
        expected_type="tool",
        expected_tool="weather",
        expected_args={"city": "New York"}
    )

    # ── 11. Case insensitivity for prefix ─────────────────────────────────
    check(
        "tool: lowercase prefix",
        'tool: shell {"command": "dir"}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "dir"}
    )

    check(
        "Tool: mixed case prefix",
        'Tool: shell {"command": "dir"}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": "dir"}
    )

    # ── 12. Malformed JSON: unescaped inner quotes (regex recovery) ────────
    #
    # This is the actual bug from live testing:
    #   LLM returns: TOOL: shell {"command": "mkdir "%USERPROFILE%\test123""}
    #   json.loads fails because of the unescaped inner quotes.
    #   Regex recovery should extract: command = mkdir "%USERPROFILE%\test123"
    #
    check(
        "Malformed JSON: unescaped quotes (mkdir userprofile)",
        'TOOL: shell {"command": "mkdir "%USERPROFILE%\\test123""}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": 'mkdir "%USERPROFILE%\\test123"'}
    )

    check(
        "Malformed JSON: unescaped quotes (dir userprofile)",
        'TOOL: shell {"command": "dir "%USERPROFILE%\\Desktop""}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": 'dir "%USERPROFILE%\\Desktop"'}
    )

    check(
        "Malformed JSON: unescaped quotes (start chrome URL)",
        'TOOL: shell {"command": "start chrome "https://google.com""}',
        expected_type="tool",
        expected_tool="shell",
        expected_args={"command": 'start chrome "https://google.com"'}
    )

    # ── Summary ───────────────────────────────────────────────────────────
    print(f"\n{'-' * 60}")
    print(f"  Results: {passed}/{total} passed, {failed} failed")
    print(f"{'-' * 60}\n")

    return failed == 0


if __name__ == "__main__":
    success = test_all()
    sys.exit(0 if success else 1)
