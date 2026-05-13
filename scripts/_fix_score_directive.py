#!/usr/bin/env python3
"""Helper function to fix %%score directive format.

This function parses an existing %%score line and ensures:
1. Braces { } and pipe | structure is preserved
2. Each voice group has parentheses
3. Voice names are kept as-is (no normalization)

Examples:
  { 1 | 2 } → { (1) | (2) }
  { (1 3 5) | (2 4) } → { (1 3 5) | (2 4) } (unchanged)
  { 1 3 | 2 } → { (1 3) | (2) }
  (1) (2) → (1) (2) (unchanged)
"""
import re


def _fix_score_directive(score_line: str, voice_order: list) -> str:
    """Fix %%score directive format while preserving structure.

    Args:
        score_line: Original %%score line (e.g., "%%score { 1 | 2 }")
        voice_order: List of voice names in order (used as fallback)

    Returns:
        Fixed %%score line with proper parentheses
    """
    # Remove %%score prefix
    content = re.sub(r"^\s*%%score\s+", "", score_line.strip())

    # Check if it has braces (grand staff format)
    brace_match = re.search(r"\{([^}]*)\}", content)

    if brace_match:
        # Grand staff format: { ... | ... }
        inner = brace_match.group(1)
        groups = inner.split("|")
        fixed_groups = []

        for group in groups:
            group = group.strip()
            # Check if already has parentheses
            if group.startswith("(") and group.endswith(")"):
                # Already has parentheses, keep as-is
                fixed_groups.append(group)
            else:
                # Add parentheses
                fixed_groups.append(f"({group})")

        return f"%%score {{ {' | '.join(fixed_groups)} }}"

    else:
        # Simple format: (1) (2) or 1 2
        # Split by whitespace and ensure each voice/group has parentheses
        tokens = content.split()
        fixed_tokens = []

        for token in tokens:
            if token.startswith("(") and token.endswith(")"):
                # Already has parentheses
                fixed_tokens.append(token)
            else:
                # Add parentheses
                fixed_tokens.append(f"({token})")

        return f"%%score {' '.join(fixed_tokens)}"
