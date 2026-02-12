"""
Web Chat Interface

Browser-based chat with WebSocket for real-time communication.
Gate G Phase 2: Web chat interface.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Set

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

_web_dir = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(_web_dir / "templates"),
    static_folder=str(_web_dir / "static"),
)
app.config["SECRET_KEY"] = "archi-secret-key-change-in-production"

try:
    from flask_cors import CORS
    CORS(app)
except ImportError:
    pass

socketio = SocketIO(app, cors_allowed_origins="*")

# Global state
_goal_manager: Optional[Any] = None
_router: Optional[Any] = None
_heartbeat: Optional[Any] = None
_dream_cycle: Optional[Any] = None
_active_connections: Set[str] = set()


def _get_router():
    """Lazy-load ModelRouter on first use."""
    global _router
    if _router is None:
        try:
            import src.core.cuda_bootstrap  # noqa: F401
            from src.models.router import ModelRouter
            _router = ModelRouter()
            logger.info("Model router initialized for web chat")
        except Exception as e:
            logger.warning("Model router not available: %s", e)
    return _router


def clear_router_cache() -> bool:
    """Clear the router's query cache. Returns True if cleared."""
    global _router
    if _router is not None and hasattr(_router, "_cache"):
        _router._cache.clear_all()
        logger.info("Web chat router cache cleared")
        return True
    return False


def init_web_chat(
    goal_mgr: Optional[Any] = None,
    heartbeat: Optional[Any] = None,
    dream_cycle: Optional[Any] = None,
) -> None:
    """Initialize web chat with service components."""
    global _goal_manager, _heartbeat, _dream_cycle
    _goal_manager = goal_mgr
    _heartbeat = heartbeat
    _dream_cycle = dream_cycle
    logger.info("Web chat initialized")


@app.route("/")
def index() -> str:
    """Redirect to chat."""
    from flask import redirect
    return redirect("/chat", code=302)


@app.route("/chat")
def chat_page() -> str:
    """Chat interface page."""
    return render_template("chat.html")


@app.route("/clear-cache", methods=["GET"])
@app.route("/api/clear-cache", methods=["GET"])
def api_clear_cache() -> tuple:
    """Clear the router's query cache (removes cached Grok responses)."""
    from flask import jsonify
    if clear_router_cache():
        return jsonify({"success": True, "message": "Cache cleared"}), 200
    return jsonify({"success": False, "message": "Router not initialized yet"}), 200


@socketio.on("connect")
def handle_connect() -> None:
    """Handle client connection."""
    session_id = request.sid
    _active_connections.add(session_id)
    _trace_chat("CONNECT: client connected")
    logger.info("Web chat client connected: %s", session_id)

    emit("message", {
        "type": "system",
        "content": "Connected to Archi! How can I help you?",
        "timestamp": datetime.now().isoformat(),
    })


@socketio.on("disconnect")
def handle_disconnect() -> None:
    """Handle client disconnection."""
    session_id = request.sid
    _active_connections.discard(session_id)
    logger.info("Web chat client disconnected: %s", session_id)


def _trace_chat(msg: str) -> None:
    """Trace chat flow to file (thread-safe, survives terminal buffering)."""
    try:
        from pathlib import Path
        trace_file = Path(__file__).resolve().parent.parent.parent / "logs" / "chat_trace.log"
        trace_file.parent.mkdir(parents=True, exist_ok=True)
        with open(trace_file, "a", encoding="utf-8") as f:
            from datetime import datetime
            f.write(f"{datetime.now().isoformat()} {msg}\n")
    except Exception as e:
        import sys
        print(f"TRACE ERROR: {e}", file=sys.stderr, flush=True)


@socketio.on("chat_message")
def handle_chat_message(data: dict) -> None:
    """Handle incoming chat message."""
    try:
        message = (data.get("message") or "").strip()

        if not message:
            return

        _trace_chat("CHAT: message received")
        print("=== CHAT: message received ===", flush=True)
        logger.info("Web chat received: %s", message[:80])

        if _heartbeat is not None:
            _heartbeat.record_user_interaction()
        if _dream_cycle is not None:
            _dream_cycle.mark_activity()  # Prevent dream cycles while chatting

        # Echo user message
        emit("message", {
            "type": "user",
            "content": message,
            "timestamp": datetime.now().isoformat(),
        })

        # Typing indicator
        emit("typing", {"typing": True})

        router = _get_router()
        if not router:
            emit("typing", {"typing": False})
            emit("message", {
                "type": "error",
                "content": "AI model not available. Configure GROK_API_KEY or local model.",
                "timestamp": datetime.now().isoformat(),
            })
            return

        from src.interfaces.action_executor import process_message as execute_action

        _trace_chat("CHAT: calling action_executor")
        print("=== CHAT: calling action_executor ===", flush=True)
        response_text, actions_taken, cost = execute_action(message, router)
        _trace_chat(f"CHAT: returned len={len(response_text)} preview={response_text[:80]!r}")
        print("=== CHAT: action_executor returned ===", flush=True)

        emit("typing", {"typing": False})

        if actions_taken:
            action_lines = "\n\n".join(f"[OK] {a['description']}" for a in actions_taken)
            response_text += f"\n\n{action_lines}"

        emit("message", {
            "type": "assistant",
            "content": response_text,
            "timestamp": datetime.now().isoformat(),
            "cost": cost,
            "provider": "archi",
        })

        # Refresh cost display
        handle_get_costs()

    except Exception as e:
        logger.error("Web chat error: %s", e, exc_info=True)
        emit("typing", {"typing": False})
        emit("message", {
            "type": "error",
            "content": f"Sorry, I encountered an error: {str(e)}",
            "timestamp": datetime.now().isoformat(),
        })


@socketio.on("create_goal")
def handle_create_goal(data: dict) -> None:
    """Handle goal creation request."""
    try:
        description = (data.get("description") or "").strip()
        priority = int(data.get("priority", 5))

        if not description:
            emit("goal_result", {"success": False, "error": "Description required"})
            return

        if _dream_cycle is not None:
            _dream_cycle.mark_activity()

        if _goal_manager is None:
            emit("goal_result", {"success": False, "error": "Goal manager not initialized"})
            return

        goal = _goal_manager.create_goal(
            description=description,
            user_intent="User request via web chat",
            priority=priority,
        )

        emit("goal_result", {
            "success": True,
            "goal_id": goal.goal_id,
            "message": f"Goal created: {goal.goal_id}",
        })

        emit("message", {
            "type": "system",
            "content": f"[OK] Goal created: {description}\nArchi will work on this during dream cycles.",
            "timestamp": datetime.now().isoformat(),
        })

    except Exception as e:
        logger.error("Goal creation error: %s", e, exc_info=True)
        emit("goal_result", {"success": False, "error": str(e)})


@socketio.on("get_costs")
def handle_get_costs() -> None:
    """Send cost summary."""
    try:
        from src.monitoring.cost_tracker import get_cost_tracker

        tracker = get_cost_tracker()
        summary = tracker.get_summary("today")

        emit("costs", {
            "today": {
                "spent": summary.get("total_cost", 0),
                "budget": summary.get("budget", 5),
                "percentage": summary.get("percentage", 0),
            },
        })

    except Exception as e:
        logger.error("Cost fetch error: %s", e, exc_info=True)
        emit("costs", {"today": {"spent": 0, "budget": 5, "percentage": 0}})


def run_web_chat(host: str = "127.0.0.1", port: int = 5001) -> None:
    """Run the web chat server (blocking)."""
    _trace_chat(f"WEB_CHAT: server starting on {host}:{port}")
    logger.info("Starting web chat on %s:%s", host, port)
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
