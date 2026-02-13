"""
Web Chat Interface

Browser-based chat with WebSocket for real-time communication.
Supports text messages and image upload (file + clipboard paste) for vision analysis.
"""

import base64
import logging
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Set

from flask import Flask, jsonify, render_template, request
from flask_socketio import SocketIO, emit

logger = logging.getLogger(__name__)

_web_dir = Path(__file__).resolve().parent
app = Flask(
    __name__,
    template_folder=str(_web_dir / "templates"),
    static_folder=str(_web_dir / "static"),
)
import os as _os
app.config["SECRET_KEY"] = _os.environ.get("ARCHI_SECRET_KEY", _os.urandom(32).hex())

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
    """Return shared ModelRouter (set via init_web_chat) or lazy-load on first use."""
    global _router
    if _router is None:
        try:
            import src.core.cuda_bootstrap  # noqa: F401
            from src.models.router import ModelRouter
            _router = ModelRouter()
            logger.info("Model router initialized for web chat (lazy)")
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
    router: Optional[Any] = None,
) -> None:
    """Initialize web chat with service components."""
    global _goal_manager, _heartbeat, _dream_cycle, _router
    _goal_manager = goal_mgr
    _heartbeat = heartbeat
    _dream_cycle = dream_cycle
    if router is not None:
        _router = router
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
    if clear_router_cache():
        return jsonify({"success": True, "message": "Cache cleared"}), 200
    return jsonify({"success": False, "message": "Router not initialized yet"}), 200


@app.route("/api/upload-image", methods=["POST"])
def api_upload_image() -> tuple:
    """Accept an image file upload. Returns a temporary path for vision analysis."""
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No image file provided"}), 400
    file = request.files["image"]
    if not file.filename:
        return jsonify({"success": False, "error": "Empty filename"}), 400
    # Validate image type
    allowed = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed:
        return jsonify({"success": False, "error": f"Unsupported format: {ext}"}), 400
    # Save to temp directory
    upload_dir = _web_dir.parent.parent / "data" / "uploads"
    upload_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{uuid.uuid4().hex}{ext}"
    dest = upload_dir / fname
    file.save(str(dest))
    logger.info("Image uploaded: %s (%s)", fname, ext)
    return jsonify({"success": True, "path": str(dest), "filename": fname}), 200


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


def _save_base64_image(data_url: str) -> Optional[str]:
    """Decode a base64 data URL (from clipboard paste) and save to disk. Returns file path."""
    try:
        # data:image/png;base64,iVBOR...
        if "," not in data_url:
            return None
        header, encoded = data_url.split(",", 1)
        # Determine extension from MIME type
        ext = ".png"
        if "image/jpeg" in header:
            ext = ".jpg"
        elif "image/gif" in header:
            ext = ".gif"
        elif "image/webp" in header:
            ext = ".webp"
        elif "image/bmp" in header:
            ext = ".bmp"
        upload_dir = _web_dir.parent.parent / "data" / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}{ext}"
        dest = upload_dir / fname
        with open(dest, "wb") as f:
            f.write(base64.b64decode(encoded))
        logger.info("Saved clipboard image: %s", fname)
        return str(dest)
    except Exception as e:
        logger.error("Failed to save base64 image: %s", e)
        return None


@socketio.on("chat_message")
def handle_chat_message(data: dict) -> None:
    """Handle incoming chat message (text, or text + image)."""
    try:
        message = (data.get("message") or "").strip()
        image_data = data.get("image_data")  # base64 data URL from clipboard paste
        image_path = data.get("image_path")  # server path from file upload

        if not message and not image_data and not image_path:
            return

        _trace_chat("CHAT: message received")
        logger.info("Web chat received: %s (has_image=%s)", message[:80], bool(image_data or image_path))

        if _heartbeat is not None:
            _heartbeat.record_user_interaction()
        if _dream_cycle is not None:
            _dream_cycle.mark_activity()

        # Resolve image to a file path
        resolved_image_path = None
        if image_data:
            resolved_image_path = _save_base64_image(image_data)
        elif image_path:
            resolved_image_path = image_path

        # Echo user message (include thumbnail indicator if image was sent)
        user_content = message
        if resolved_image_path:
            user_content = f"[Image attached] {message}" if message else "[Image attached]"
        emit("message", {
            "type": "user",
            "content": user_content,
            "timestamp": datetime.now().isoformat(),
            "has_image": bool(resolved_image_path),
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

        # Image analysis path: use vision model
        if resolved_image_path:
            text_prompt = message or "Describe what you see in this image."
            _trace_chat(f"CHAT: vision analysis of {resolved_image_path}")
            vision_result = router.chat_with_image(text_prompt, resolved_image_path)
            cost = vision_result.get("cost_usd", 0)
            response_text = vision_result.get("text", "").strip()
            if not response_text:
                response_text = f"I couldn't analyze the image: {vision_result.get('error', 'unknown error')}"
            emit("typing", {"typing": False})
            emit("message", {
                "type": "assistant",
                "content": response_text,
                "timestamp": datetime.now().isoformat(),
                "cost": cost,
                "provider": "archi",
            })
            try:
                from src.interfaces.chat_history import append
                append("user", user_content)
                append("assistant", response_text)
            except Exception as e:
                logger.debug("Could not save chat history: %s", e)
            handle_get_costs()
            return

        # Text-only path (original flow)
        from src.interfaces.action_executor import process_message as execute_action
        from src.interfaces.chat_history import get_recent, append

        history = get_recent()
        _trace_chat("CHAT: calling action_executor")
        response_text, actions_taken, cost = execute_action(
            message, router, history=history, source="web", goal_manager=_goal_manager
        )
        _trace_chat(f"CHAT: returned len={len(response_text)} preview={response_text[:80]!r}")

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

        # Persist to chat history (survives restart)
        try:
            append("user", message)
            append("assistant", response_text)
        except Exception as e:
            logger.debug("Could not save chat history: %s", e)

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
                "budget": summary.get("budget", tracker.daily_budget),
                "percentage": summary.get("percentage", 0),
            },
        })

    except Exception as e:
        logger.error("Cost fetch error: %s", e, exc_info=True)
        from src.monitoring.cost_tracker import get_budget_limit_from_rules
        emit("costs", {"today": {"spent": 0, "budget": get_budget_limit_from_rules(), "percentage": 0}})


def run_web_chat(host: str = "127.0.0.1", port: int = 0) -> None:
    """Run the web chat server (blocking). Port defaults to rules.yaml ports.web_chat."""
    if port == 0:
        from src.utils.config import get_ports
        port = get_ports()["web_chat"]
    _trace_chat(f"WEB_CHAT: server starting on {host}:{port}")
    logger.info("Starting web chat on %s:%s", host, port)
    socketio.run(app, host=host, port=port, debug=False, allow_unsafe_werkzeug=True)
