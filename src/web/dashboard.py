"""
Archi Web Dashboard

Simple web interface for monitoring and controlling Archi.
Uses Flask for the backend and basic HTML/CSS/JS for frontend.
Gate F Phase 2: Web dashboard for monitoring and control.
"""

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, jsonify, render_template, request

logger = logging.getLogger(__name__)

# Template/static paths relative to this file
_web_dir = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(_web_dir / "templates"),
    static_folder=str(_web_dir / "static"),
)

# Optional CORS for dev
try:
    from flask_cors import CORS
    CORS(app)
except ImportError:
    pass

# Global references (set by service via init_dashboard)
_goal_manager: Optional[Any] = None
_dream_cycle: Optional[Any] = None


def init_dashboard(goal_mgr: Any, dream: Any) -> None:
    """Initialize dashboard with service components."""
    global _goal_manager, _dream_cycle
    _goal_manager = goal_mgr
    _dream_cycle = dream
    logger.info("Dashboard initialized with service components")


@app.route("/")
def index() -> str:
    """Main dashboard page."""
    return render_template("dashboard.html")


@app.route("/api/health")
def api_health():
    """Get system health status."""
    from src.monitoring.health_check import health_check

    health = health_check.check_all()
    return jsonify(health)


@app.route("/api/costs")
def api_costs():
    """Get cost information."""
    from src.monitoring.cost_tracker import get_cost_tracker

    tracker = get_cost_tracker()
    summary = tracker.get_summary("all")
    recommendations = tracker.get_recommendations()

    return jsonify({
        "summary": summary,
        "recommendations": recommendations,
    })


@app.route("/api/performance")
def api_performance():
    """Get performance metrics."""
    try:
        from src.monitoring.performance_monitor import PerformanceMonitor

        # Use module-level instance if available, else create one
        if not hasattr(api_performance, "_perf_monitor"):
            api_performance._perf_monitor = PerformanceMonitor()  # type: ignore
        stats = api_performance._perf_monitor.get_stats()  # type: ignore
        return jsonify(stats)
    except Exception as e:
        logger.debug("Performance stats unavailable: %s", e)
        return jsonify({})


@app.route("/api/goals")
def api_goals() -> tuple:
    """Get goals and tasks."""
    if _goal_manager is None:
        return jsonify({"error": "Goal manager not initialized"}), 503

    status = _goal_manager.get_status()
    return jsonify(status)


@app.route("/api/goals/create", methods=["POST"])
def api_create_goal() -> tuple:
    """Create a new goal."""
    if _goal_manager is None:
        return jsonify({"error": "Goal manager not initialized"}), 503

    data = request.get_json() or {}
    description = data.get("description")
    user_intent = data.get("user_intent", "")
    priority = data.get("priority", 5)

    if not description:
        return jsonify({"error": "Description required"}), 400

    try:
        goal = _goal_manager.create_goal(description, user_intent, priority)
        return jsonify({
            "success": True,
            "goal_id": goal.goal_id,
            "message": "Goal created successfully",
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/dream")
def api_dream_status() -> tuple:
    """Get dream cycle status."""
    if _dream_cycle is None:
        return jsonify({"error": "Dream cycle not initialized"}), 503

    status = _dream_cycle.get_status()

    # Add dream history (last 10)
    history = []
    for dream in _dream_cycle.dream_history[-10:]:
        history.append({
            "started_at": dream.get("started_at"),
            "duration": dream.get("duration_seconds", 0),
            "tasks_processed": dream.get("tasks_processed", 0),
            "interrupted": dream.get("interrupted", False),
        })

    status["history"] = history
    return jsonify(status)


def run_dashboard(host: str = "127.0.0.1", port: int = 5000) -> None:
    """Run the dashboard server (blocking)."""
    logger.info("Starting dashboard on %s:%s", host, port)
    app.run(host=host, port=port, debug=False, use_reloader=False)
