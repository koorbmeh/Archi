"""Strategic Planner — designs multi-phase implementation plans.

Session 237: Self-Extension Phase 3.

Takes a ProjectProposal (from capability assessor) + optional ResearchResult
(from research agent), uses the escalation model (Gemini 3.1 Pro) to design
multi-file, multi-phase implementation plans.  Writes design docs, breaks work
into phases executable in 1-3 dream cycles each, persists plans, and creates
goals for the current phase.

Architecture:
    CapabilityAssessor → propose_project() → ProjectProposal
    ResearchAgent      → research()        → ResearchResult
    StrategicPlanner   → create_plan()     → ImplementationPlan
                       → advance_plan()    → PhaseResult

Persistence: data/self_extension/projects.json + per-project dirs.
"""

import json
import logging
import os
import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from src.utils.paths import base_path_as_path as _base_path

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────

_PROJECTS_PATH = "data/self_extension/projects.json"
_DESIGN_DIR = "claude"  # Design docs land in claude/DESIGN_*.md
_PLAN_COST_CAP = 0.20   # USD — escalation model is more expensive
_MAX_PHASES = 8          # Hard cap on phases per project
_MAX_TASKS_PER_PHASE = 6
_MAX_ACTIVE_PROJECTS = 1  # One active self-extension project at a time
_MAX_STORED_PROJECTS = 30

_lock = threading.Lock()


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class PhaseTask:
    """A single task within a phase."""
    description: str = ""
    task_type: str = "code"    # code / test / integration / docs
    files_involved: List[str] = field(default_factory=list)
    done: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "task_type": self.task_type,
            "files_involved": self.files_involved,
            "done": self.done,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PhaseTask":
        return cls(
            description=d.get("description", ""),
            task_type=d.get("task_type", "code"),
            files_involved=d.get("files_involved", []),
            done=d.get("done", False),
        )


@dataclass
class PlanPhase:
    """One phase of an implementation plan (1-3 dream cycles)."""
    phase_number: int = 1
    title: str = ""
    description: str = ""
    tasks: List[PhaseTask] = field(default_factory=list)
    status: str = "pending"   # pending / in_progress / completed / failed
    started_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "phase_number": self.phase_number,
            "title": self.title,
            "description": self.description,
            "tasks": [t.to_dict() for t in self.tasks],
            "status": self.status,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PlanPhase":
        return cls(
            phase_number=d.get("phase_number", 1),
            title=d.get("title", ""),
            description=d.get("description", ""),
            tasks=[PhaseTask.from_dict(t) for t in d.get("tasks", [])],
            status=d.get("status", "pending"),
            started_at=d.get("started_at"),
            completed_at=d.get("completed_at"),
        )


@dataclass
class ImplementationPlan:
    """A complete multi-phase plan for a self-extension project."""
    project_id: str = ""
    title: str = ""
    description: str = ""
    gap_name: str = ""             # From CapabilityGap
    status: str = "planned"        # planned / active / completed / abandoned
    phases: List[PlanPhase] = field(default_factory=list)
    current_phase: int = 1
    design_doc_path: str = ""      # claude/DESIGN_*.md
    new_files: List[str] = field(default_factory=list)
    modified_files: List[str] = field(default_factory=list)
    integration_points: List[str] = field(default_factory=list)
    jesse_actions: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    total_cost: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "title": self.title,
            "description": self.description,
            "gap_name": self.gap_name,
            "status": self.status,
            "phases": [p.to_dict() for p in self.phases],
            "current_phase": self.current_phase,
            "design_doc_path": self.design_doc_path,
            "new_files": self.new_files,
            "modified_files": self.modified_files,
            "integration_points": self.integration_points,
            "jesse_actions": self.jesse_actions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_cost": self.total_cost,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImplementationPlan":
        return cls(
            project_id=d.get("project_id", ""),
            title=d.get("title", ""),
            description=d.get("description", ""),
            gap_name=d.get("gap_name", ""),
            status=d.get("status", "planned"),
            phases=[PlanPhase.from_dict(p) for p in d.get("phases", [])],
            current_phase=d.get("current_phase", 1),
            design_doc_path=d.get("design_doc_path", ""),
            new_files=d.get("new_files", []),
            modified_files=d.get("modified_files", []),
            integration_points=d.get("integration_points", []),
            jesse_actions=d.get("jesse_actions", []),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
            total_cost=d.get("total_cost", 0.0),
        )

    @property
    def current_phase_obj(self) -> Optional[PlanPhase]:
        """Get the current phase object."""
        for p in self.phases:
            if p.phase_number == self.current_phase:
                return p
        return None

    @property
    def is_complete(self) -> bool:
        return all(p.status == "completed" for p in self.phases)

    @property
    def progress_pct(self) -> float:
        if not self.phases:
            return 0.0
        done = sum(1 for p in self.phases if p.status == "completed")
        return done / len(self.phases)


@dataclass
class PhaseResult:
    """Result of checking/advancing a plan phase."""
    action: str = "none"    # none / started / advanced / completed / failed
    phase_number: int = 0
    message: str = ""
    goal_descriptions: List[str] = field(default_factory=list)


# ── Persistence ──────────────────────────────────────────────────────

def _projects_path() -> str:
    return str(_base_path() / _PROJECTS_PATH)


def _load_projects() -> Dict[str, Any]:
    """Load project registry from disk."""
    path = _projects_path()
    if not os.path.isfile(path):
        return {"projects": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load projects: %s", e)
        return {"projects": []}


def _save_projects(data: Dict[str, Any]) -> None:
    """Atomically write project registry to disk."""
    path = _projects_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Trim old completed/abandoned projects
    projects = data.get("projects", [])
    if len(projects) > _MAX_STORED_PROJECTS:
        # Keep active/planned, trim oldest completed
        active = [p for p in projects if p.get("status") in ("planned", "active")]
        done = [p for p in projects if p.get("status") not in ("planned", "active")]
        data["projects"] = active + done[-(
            _MAX_STORED_PROJECTS - len(active)):] if done else active
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    os.replace(tmp, path)


def _save_plan(plan: ImplementationPlan) -> None:
    """Persist a plan to the project registry."""
    with _lock:
        data = _load_projects()
        projects = data.get("projects", [])
        # Update existing or append
        found = False
        for i, p in enumerate(projects):
            if p.get("project_id") == plan.project_id:
                projects[i] = plan.to_dict()
                found = True
                break
        if not found:
            projects.append(plan.to_dict())
        data["projects"] = projects
        _save_projects(data)


# ── Helpers ──────────────────────────────────────────────────────────

def _sanitize_project_id(name: str) -> str:
    """Convert a gap name or title to a filesystem-safe project ID."""
    clean = name.lower().replace(" ", "_")
    clean = "".join(c for c in clean if c.isalnum() or c == "_")
    return clean[:40] or "project"


def _parse_json(raw: str) -> Dict[str, Any]:
    """Extract JSON object from model output."""
    raw = raw.strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start:end + 1])
        except json.JSONDecodeError:
            pass
    # Fallback: try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Failed to parse plan JSON from model output")
        return {}


# ── Architecture reading ─────────────────────────────────────────────

def _read_architecture_summary() -> str:
    """Read condensed architecture context for the planner prompt."""
    arch_path = str(_base_path() / "claude" / "ARCHITECTURE.md")
    try:
        with open(arch_path, "r", encoding="utf-8") as f:
            content = f.read()
        # Return first ~3000 chars (system overview + directory layout)
        return content[:3000]
    except Exception:
        return "(Architecture doc not available)"


def _read_existing_design_docs() -> str:
    """List existing design docs for context."""
    design_dir = str(_base_path() / "claude")
    try:
        docs = [f for f in os.listdir(design_dir)
                if f.startswith("DESIGN_") and f.endswith(".md")]
        return ", ".join(sorted(docs)) if docs else "(none)"
    except Exception:
        return "(unknown)"


# ── Plan creation prompt ─────────────────────────────────────────────

_PLAN_PROMPT = """You are Archi's strategic planner. Design a multi-phase implementation plan
for a new capability. You have deep knowledge of Archi's architecture.

ARCHITECTURE CONTEXT:
{architecture}

EXISTING DESIGN DOCS: {existing_designs}

PROJECT TO PLAN:
Title: {title}
Description: {description}
Gap being closed: {gap_name}
Jesse must provide: {jesse_actions}

{research_context}

Design the implementation plan. Consider:
1. What NEW files/modules need to be created (with paths in src/ or config/)
2. What EXISTING files need modification (router, dispatcher, heartbeat, etc.)
3. How it integrates with existing systems (registration pattern, config, etc.)
4. What tests are needed (unit tests in tests/unit/)
5. Break into phases, each executable in 1-3 dream cycles (~$0.50 budget each)

CONSTRAINTS:
- Protected files (CANNOT modify): src/core/plan_executor/*, src/core/safety_controller.py
- Source code changes in dream mode require Discord approval
- Follow existing patterns: snake_case, logging module, dataclasses, etc.
- Each phase should be independently testable
- Phase 1 should create the core module + basic interface
- Later phases add integration (router, dispatcher, heartbeat), tests, docs

Return JSON:
{{
  "description": "2-3 sentence overview of the solution",
  "new_files": ["src/tools/example.py", ...],
  "modified_files": ["src/interfaces/action_dispatcher.py", ...],
  "integration_points": ["router intent detection", "dispatcher handler", ...],
  "phases": [
    {{
      "phase_number": 1,
      "title": "Core module",
      "description": "What this phase builds",
      "tasks": [
        {{"description": "Create src/tools/example.py with ExampleClass", "task_type": "code", "files_involved": ["src/tools/example.py"]}},
        {{"description": "Add unit tests", "task_type": "test", "files_involved": ["tests/unit/test_example.py"]}}
      ]
    }}
  ],
  "jesse_actions": ["Sign up for X API", "Add Y_API_KEY to .env"]
}}"""


# ── Design doc generation ────────────────────────────────────────────

_DESIGN_DOC_PROMPT = """Write a design doc for the following implementation plan. Use markdown format.

PROJECT: {title}
DESCRIPTION: {description}
GAP: {gap_name}

PLAN SUMMARY:
{plan_summary}

{research_context}

Write a concise design doc following this structure:
1. Problem / Gap
2. Solution overview
3. Architecture (files, data flow)
4. Implementation phases (numbered)
5. Integration points
6. What Jesse needs to provide
7. Safety considerations

Keep it under 150 lines. Be specific about file paths and function names.
Output ONLY the markdown content (no JSON wrapping)."""


# ── Core class ───────────────────────────────────────────────────────

class StrategicPlanner:
    """Designs multi-phase implementation plans for new capabilities.

    Uses the escalation model (Gemini 3.1 Pro) for complex architectural
    reasoning.  Creates design docs, breaks work into phases, and generates
    goals for execution by the existing goal/task system.
    """

    def __init__(self, router: Any) -> None:
        self._router = router

    def _model_call(self, prompt: str, system: str = "", max_tokens: int = 1200,
                    use_escalation: bool = True) -> tuple:
        """Make a model call, optionally via escalation tier.

        Returns (text, cost).
        """
        if use_escalation:
            try:
                with self._router.escalate_for_task("gemini-3.1-pro") as switch:
                    result = self._router.generate(
                        system_prompt=system or "You are an expert software architect.",
                        prompt=prompt,
                        max_tokens=max_tokens,
                        temperature=0.3,
                        skip_web_search=True,
                    )
                    return (result.get("text", ""), result.get("cost_usd", 0.0))
            except Exception as e:
                logger.warning("Escalation failed, using default model: %s", e)

        # Fallback to default model
        result = self._router.generate(
            system_prompt=system or "You are an expert software architect.",
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=0.3,
            skip_web_search=True,
        )
        return (result.get("text", ""), result.get("cost_usd", 0.0))

    def create_plan(
        self,
        project_title: str,
        project_description: str,
        gap_name: str = "",
        jesse_actions: Optional[List[str]] = None,
        research_result: Optional[Any] = None,
    ) -> Optional[ImplementationPlan]:
        """Design a multi-phase implementation plan.

        Args:
            project_title: Name of the project.
            project_description: What needs to be built.
            gap_name: The capability gap being addressed.
            jesse_actions: Things Jesse needs to do.
            research_result: Optional ResearchResult with prior research.

        Returns:
            ImplementationPlan or None on failure.
        """
        t0 = time.time()
        total_cost = 0.0
        project_id = _sanitize_project_id(gap_name or project_title)

        # Check for existing active project
        active = get_active_project()
        if active and active.get("status") == "active":
            logger.warning("Cannot create plan — active project exists: %s",
                           active.get("project_id"))
            return None

        # Build research context
        research_context = ""
        if research_result:
            if hasattr(research_result, "format_for_user"):
                research_context = f"PRIOR RESEARCH:\n{research_result.format_for_user()}"
            elif hasattr(research_result, "conclusion"):
                research_context = f"PRIOR RESEARCH:\n{research_result.conclusion}"
            elif isinstance(research_result, dict):
                research_context = f"PRIOR RESEARCH:\n{research_result.get('conclusion', '')}"

        # Read architecture context
        arch_summary = _read_architecture_summary()
        existing_designs = _read_existing_design_docs()

        # Step 1: Generate the plan via escalation model
        plan_prompt = _PLAN_PROMPT.format(
            architecture=arch_summary,
            existing_designs=existing_designs,
            title=project_title,
            description=project_description,
            gap_name=gap_name or "(general improvement)",
            jesse_actions=", ".join(jesse_actions or []) or "nothing",
            research_context=research_context or "(no prior research)",
        )

        raw, cost = self._model_call(plan_prompt, max_tokens=1500)
        total_cost += cost

        if total_cost > _PLAN_COST_CAP:
            logger.warning("Plan cost $%.4f exceeded cap $%.2f", total_cost, _PLAN_COST_CAP)

        plan_data = _parse_json(raw)
        if not plan_data or "phases" not in plan_data:
            logger.error("Failed to parse plan from model output")
            return None

        # Build phases
        phases = []
        for pd in plan_data.get("phases", [])[:_MAX_PHASES]:
            tasks = []
            for td in pd.get("tasks", [])[:_MAX_TASKS_PER_PHASE]:
                tasks.append(PhaseTask(
                    description=td.get("description", ""),
                    task_type=td.get("task_type", "code"),
                    files_involved=td.get("files_involved", []),
                ))
            phases.append(PlanPhase(
                phase_number=pd.get("phase_number", len(phases) + 1),
                title=pd.get("title", f"Phase {len(phases) + 1}"),
                description=pd.get("description", ""),
                tasks=tasks,
            ))

        if not phases:
            logger.error("Plan has no phases")
            return None

        now = datetime.now().isoformat()
        plan = ImplementationPlan(
            project_id=project_id,
            title=project_title,
            description=plan_data.get("description", project_description),
            gap_name=gap_name,
            status="planned",
            phases=phases,
            current_phase=1,
            new_files=plan_data.get("new_files", []),
            modified_files=plan_data.get("modified_files", []),
            integration_points=plan_data.get("integration_points", []),
            jesse_actions=plan_data.get("jesse_actions", jesse_actions or []),
            created_at=now,
            updated_at=now,
            total_cost=total_cost,
        )

        # Step 2: Generate design doc
        design_doc = self._generate_design_doc(plan, research_context)
        if design_doc:
            doc_filename = f"DESIGN_{project_id.upper()}.md"
            doc_path = str(_base_path() / _DESIGN_DIR / doc_filename)
            try:
                with open(doc_path, "w", encoding="utf-8") as f:
                    f.write(design_doc)
                plan.design_doc_path = f"claude/{doc_filename}"
                logger.info("Design doc written: %s", plan.design_doc_path)
            except Exception as e:
                logger.error("Failed to write design doc: %s", e)

        # Persist
        _save_plan(plan)

        elapsed = time.time() - t0
        logger.info(
            "Strategic plan created: %s — %d phases, %d new files, $%.4f, %.1fs",
            project_id, len(phases), len(plan.new_files), total_cost, elapsed,
        )
        return plan

    def _generate_design_doc(self, plan: ImplementationPlan,
                             research_context: str = "") -> Optional[str]:
        """Generate a markdown design doc for the plan."""
        plan_summary_parts = []
        for p in plan.phases:
            task_list = ", ".join(t.description[:60] for t in p.tasks[:4])
            plan_summary_parts.append(
                f"Phase {p.phase_number}: {p.title} — {p.description[:100]}\n"
                f"  Tasks: {task_list}"
            )
        plan_summary = "\n".join(plan_summary_parts)

        prompt = _DESIGN_DOC_PROMPT.format(
            title=plan.title,
            description=plan.description,
            gap_name=plan.gap_name,
            plan_summary=plan_summary,
            research_context=research_context or "(no prior research)",
        )

        try:
            text, cost = self._model_call(prompt, max_tokens=2000)
            plan.total_cost += cost
            # Strip markdown fences if present
            if text.startswith("```"):
                lines = text.split("\n")
                text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
            return text if len(text) > 50 else None
        except Exception as e:
            logger.error("Design doc generation failed: %s", e)
            return None

    def activate_plan(self, project_id: str) -> PhaseResult:
        """Mark a planned project as active (after Jesse approval).

        Returns PhaseResult with Phase 1 goal descriptions so the caller
        can create project-tagged goals.  Returns action="none" on failure.
        """
        with _lock:
            data = _load_projects()
            for p in data.get("projects", []):
                if p.get("project_id") == project_id:
                    if p.get("status") != "planned":
                        logger.warning("Cannot activate %s: status is %s",
                                       project_id, p.get("status"))
                        return PhaseResult(action="none",
                                           message=f"Cannot activate: status is {p.get('status')}")
                    p["status"] = "active"
                    p["updated_at"] = datetime.now().isoformat()
                    # Start phase 1 and collect goal descriptions
                    goal_descs = []
                    for phase in p.get("phases", []):
                        if phase.get("phase_number") == 1:
                            phase["status"] = "in_progress"
                            phase["started_at"] = datetime.now().isoformat()
                            goal_descs = [
                                t.get("description", "")
                                for t in phase.get("tasks", [])
                                if not t.get("done") and t.get("description")
                            ]
                            break
                    _save_projects(data)
                    logger.info("Project activated: %s", project_id)
                    return PhaseResult(
                        action="started",
                        phase_number=1,
                        message=f"Project activated, Phase 1 started",
                        goal_descriptions=goal_descs,
                    )
            return PhaseResult(action="none", message="Project not found")

    def advance_plan(self, project_id: str) -> PhaseResult:
        """Check current phase status and advance if complete.

        Called from heartbeat when a self-extension project is active.

        Returns:
            PhaseResult describing what happened.
        """
        with _lock:
            data = _load_projects()
            for p_data in data.get("projects", []):
                if p_data.get("project_id") != project_id:
                    continue
                if p_data.get("status") != "active":
                    return PhaseResult(action="none", message="Project not active")

                plan = ImplementationPlan.from_dict(p_data)
                current = plan.current_phase_obj

                if not current:
                    return PhaseResult(action="none", message="No current phase found")

                # Check if current phase tasks are all done
                if current.status == "in_progress":
                    all_done = all(t.done for t in current.tasks)
                    if not all_done:
                        return PhaseResult(
                            action="none",
                            phase_number=current.phase_number,
                            message=f"Phase {current.phase_number} in progress "
                                    f"({sum(1 for t in current.tasks if t.done)}/{len(current.tasks)} tasks done)",
                        )
                    # Phase complete — mark it
                    current.completed_at = datetime.now().isoformat()
                    current.status = "completed"

                # Find next pending phase
                next_phase = None
                for ph in plan.phases:
                    if ph.status == "pending":
                        next_phase = ph
                        break

                if not next_phase:
                    # All phases done — project complete
                    plan.status = "completed"
                    plan.updated_at = datetime.now().isoformat()
                    # Update in data
                    for i, pd in enumerate(data["projects"]):
                        if pd.get("project_id") == project_id:
                            data["projects"][i] = plan.to_dict()
                            break
                    _save_projects(data)
                    return PhaseResult(
                        action="completed",
                        phase_number=current.phase_number if current else 0,
                        message=f"Project '{plan.title}' completed! All {len(plan.phases)} phases done.",
                    )

                # Advance to next phase
                next_phase.status = "in_progress"
                next_phase.started_at = datetime.now().isoformat()
                plan.current_phase = next_phase.phase_number
                plan.updated_at = datetime.now().isoformat()

                # Generate goal descriptions for the new phase's tasks
                goal_descs = [t.description for t in next_phase.tasks if not t.done]

                # Update in data
                for i, pd in enumerate(data["projects"]):
                    if pd.get("project_id") == project_id:
                        data["projects"][i] = plan.to_dict()
                        break
                _save_projects(data)

                return PhaseResult(
                    action="advanced",
                    phase_number=next_phase.phase_number,
                    message=f"Advanced to Phase {next_phase.phase_number}: {next_phase.title}",
                    goal_descriptions=goal_descs,
                )

        return PhaseResult(action="none", message="Project not found")

    def mark_phase_task_done(self, project_id: str, phase_number: int,
                             task_index: int) -> bool:
        """Mark a specific task within a phase as completed.

        Args:
            project_id: The project identifier.
            phase_number: Which phase.
            task_index: 0-based index of the task in the phase.

        Returns:
            True if marked successfully.
        """
        with _lock:
            data = _load_projects()
            for p in data.get("projects", []):
                if p.get("project_id") != project_id:
                    continue
                for phase in p.get("phases", []):
                    if phase.get("phase_number") != phase_number:
                        continue
                    tasks = phase.get("tasks", [])
                    if 0 <= task_index < len(tasks):
                        tasks[task_index]["done"] = True
                        p["updated_at"] = datetime.now().isoformat()
                        _save_projects(data)
                        return True
            return False

    def fail_phase(self, project_id: str, phase_number: int,
                   reason: str = "") -> PhaseResult:
        """Mark a phase as failed and pause the project.

        Called when a project-linked goal fails — propagates the failure
        upward so the project pauses rather than silently continuing.

        Args:
            project_id: The project identifier.
            phase_number: Which phase failed.
            reason: Human-readable failure reason.

        Returns:
            PhaseResult with action="failed".
        """
        with _lock:
            data = _load_projects()
            for p in data.get("projects", []):
                if p.get("project_id") != project_id:
                    continue
                if p.get("status") != "active":
                    return PhaseResult(
                        action="none",
                        message=f"Cannot fail phase: project status is {p.get('status')}",
                    )
                for phase in p.get("phases", []):
                    if phase.get("phase_number") != phase_number:
                        continue
                    phase["status"] = "failed"
                    phase["completed_at"] = datetime.now().isoformat()
                p["status"] = "paused"
                p["updated_at"] = datetime.now().isoformat()
                if reason:
                    p["pause_reason"] = reason
                _save_projects(data)
                logger.warning(
                    "Project %s paused — Phase %d failed: %s",
                    project_id, phase_number, reason or "(no reason)",
                )
                return PhaseResult(
                    action="failed",
                    phase_number=phase_number,
                    message=f"Phase {phase_number} failed: {reason}. Project paused.",
                )
            return PhaseResult(action="none", message="Project not found")

    def resume_project(self, project_id: str) -> PhaseResult:
        """Resume a paused project by retrying the failed phase.

        Resets the failed phase to in_progress and sets project back to active.
        """
        with _lock:
            data = _load_projects()
            for p in data.get("projects", []):
                if p.get("project_id") != project_id:
                    continue
                if p.get("status") != "paused":
                    return PhaseResult(
                        action="none",
                        message=f"Cannot resume: project status is {p.get('status')}",
                    )
                # Find the failed phase and reset it
                goal_descs = []
                for phase in p.get("phases", []):
                    if phase.get("status") == "failed":
                        phase["status"] = "in_progress"
                        phase["started_at"] = datetime.now().isoformat()
                        phase.pop("completed_at", None)
                        # Reset undone tasks for retry
                        goal_descs = [
                            t.get("description", "")
                            for t in phase.get("tasks", [])
                            if not t.get("done") and t.get("description")
                        ]
                        break
                p["status"] = "active"
                p.pop("pause_reason", None)
                p["updated_at"] = datetime.now().isoformat()
                _save_projects(data)
                logger.info("Project %s resumed", project_id)
                return PhaseResult(
                    action="started",
                    phase_number=p.get("current_phase", 1),
                    message=f"Project resumed, retrying phase",
                    goal_descriptions=goal_descs,
                )
            return PhaseResult(action="none", message="Project not found")

    def abandon_project(self, project_id: str, reason: str = "") -> bool:
        """Mark a project as abandoned."""
        with _lock:
            data = _load_projects()
            for p in data.get("projects", []):
                if p.get("project_id") == project_id:
                    p["status"] = "abandoned"
                    p["updated_at"] = datetime.now().isoformat()
                    if reason:
                        p["abandon_reason"] = reason
                    _save_projects(data)
                    logger.info("Project abandoned: %s (%s)", project_id, reason or "no reason")
                    return True
            return False


# ── Module-level accessors ───────────────────────────────────────────

def get_active_project() -> Optional[Dict[str, Any]]:
    """Get the currently active self-extension project, if any."""
    data = _load_projects()
    for p in data.get("projects", []):
        if p.get("status") == "active":
            return p
    return None


def get_planned_projects() -> List[Dict[str, Any]]:
    """Get projects awaiting Jesse's approval."""
    data = _load_projects()
    return [p for p in data.get("projects", []) if p.get("status") == "planned"]


def get_project(project_id: str) -> Optional[Dict[str, Any]]:
    """Get a specific project by ID."""
    data = _load_projects()
    for p in data.get("projects", []):
        if p.get("project_id") == project_id:
            return p
    return None


def get_paused_projects() -> List[Dict[str, Any]]:
    """Get projects that are paused due to phase failure."""
    data = _load_projects()
    return [p for p in data.get("projects", []) if p.get("status") == "paused"]


def get_project_stats() -> Dict[str, Any]:
    """Summary stats for diagnostics."""
    data = _load_projects()
    projects = data.get("projects", [])
    by_status = {}
    for p in projects:
        s = p.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
    active = get_active_project()
    return {
        "total_projects": len(projects),
        "by_status": by_status,
        "active_project": active.get("project_id") if active else None,
        "active_phase": active.get("current_phase") if active else None,
    }


def format_plan_for_discord(plan: ImplementationPlan) -> str:
    """Format a plan as a Discord-friendly summary."""
    parts = [f"**Project Plan: {plan.title}**"]
    parts.append(plan.description[:200])

    parts.append(f"\n**Phases ({len(plan.phases)}):**")
    for p in plan.phases:
        status_icon = {"pending": "⬜", "in_progress": "🔵", "completed": "✅",
                       "failed": "❌"}.get(p.status, "⬜")
        parts.append(f"{status_icon} Phase {p.phase_number}: {p.title}")

    if plan.new_files:
        parts.append(f"\n**New files:** {', '.join(plan.new_files[:5])}")
    if plan.jesse_actions:
        parts.append(f"\n**Jesse needs to:** {'; '.join(plan.jesse_actions[:3])}")
    parts.append(f"\nWant me to start? Reply 'go for it' to activate.")
    return "\n".join(parts)
