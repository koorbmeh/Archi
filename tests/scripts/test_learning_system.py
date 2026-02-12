import os
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))
os.chdir(_root)

import src.core.cuda_bootstrap  # noqa: F401 - CUDA path for model loading
import logging
from src.core.learning_system import LearningSystem
from src.models.local_model import LocalModel

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s"
)

print("Learning System Test")
print("=" * 60)

# Initialize
learning = LearningSystem()
model = LocalModel()

# Record some experiences
print("\n1. Recording experiences...")

learning.record_success(
    context="Creating Python script",
    action="Used clear variable names and comments",
    outcome="Code was easy to understand and maintain",
)

learning.record_success(
    context="Debugging error",
    action="Added detailed logging statements",
    outcome="Found bug quickly",
)

learning.record_failure(
    context="File processing",
    action="Didn't validate input format",
    outcome="Script crashed on malformed data",
    lesson="Always validate input before processing",
)

learning.record_feedback(
    context="Generated report",
    action="Used technical jargon",
    feedback="User prefers simple, clear language",
)

learning.record_success(
    context="CSV analysis",
    action="Validated column types before processing",
    outcome="Handled edge cases without errors",
)

# Track some metrics
print("\n2. Tracking performance metrics...")
for rate in [0.7, 0.75, 0.8, 0.82, 0.85, 0.88, 0.9, 0.92]:
    learning.track_metric("task_completion_rate", rate)

learning.track_metric("avg_task_duration_minutes", 25)
learning.track_metric("avg_task_duration_minutes", 22)
learning.track_metric("avg_task_duration_minutes", 20)

# Check trends
print("\n3. Analyzing metric trends...")
trend = learning.get_metric_trend("task_completion_rate")
print(f"Task completion rate trend: {trend}")

# Extract patterns
print("\n4. Extracting patterns from experiences...")
patterns = learning.extract_patterns(model)

if patterns:
    print(f"\nExtracted {len(patterns)} patterns:")
    for i, pattern in enumerate(patterns, 1):
        print(f"  {i}. {pattern}")

# Get improvement suggestions
print("\n5. Getting improvement suggestions...")
suggestions = learning.get_improvement_suggestions(model)

if suggestions:
    print(f"\nImprovement suggestions:")
    for i, suggestion in enumerate(suggestions, 1):
        print(f"  {i}. {suggestion}")

# Summary
print("\n6. Learning summary:")
summary = learning.get_summary()
print(f"Total experiences: {summary['total_experiences']}")
print(f"Success rate: {summary['success_rate']:.1f}%")
print(f"Patterns extracted: {summary['patterns_extracted']}")
print(f"Tracked metrics: {', '.join(summary['tracked_metrics'])}")

print("\n[OK] Learning system working!")
print("State saved to data/experiences.json")
