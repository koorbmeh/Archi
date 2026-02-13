r"""
Create initial Health Optimization analysis goal.

Run from project root: .\venv\Scripts\python.exe scripts\create_health_goal.py
"""

import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

from src.core.goal_manager import GoalManager

GOAL_DESCRIPTION = """Analyze Health Optimization project and create synthesis:

1. Read all files from all AI contributors (ChatGPT, Claude, Gemini, Grok)
2. Identify common themes and strong consensus
3. Note contradictions or disagreements between AIs
4. Create synthesized document combining best ideas from each
5. Identify gaps needing research
6. Create prioritized action plan

Location: workspace/projects/Health_Optimization/
Output: Create SYNTHESIS.md with findings"""


def main() -> None:
    goal_manager = GoalManager()
    goal = goal_manager.create_goal(
        description=GOAL_DESCRIPTION,
        user_intent="Health Optimization project analysis - multi-AI synthesis",
        priority=8,
    )
    goal_manager.save_state()
    print(f"Goal created: {goal.goal_id}")
    print(f"  Description: {goal.description[:80]}...")
    print(f"  Priority: {goal.priority}")
    print("\nArchi will work on this during dream cycles!")


if __name__ == "__main__":
    main()
