"""Tests for the Capability Assessor (Self-Extension Phase 2)."""

import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── Data class tests ─────────────────────────────────────────────────


class TestCapabilityGap:
    """Tests for CapabilityGap dataclass."""

    def test_to_dict(self):
        from src.core.capability_assessor import CapabilityGap
        gap = CapabilityGap(
            name="music_gen",
            description="Cannot generate music",
            evidence=["High interest: music production"],
            impact=0.8,
            category="content",
            requires_from_jesse="Suno API key",
        )
        d = gap.to_dict()
        assert d["name"] == "music_gen"
        assert d["impact"] == 0.8
        assert d["category"] == "content"
        assert len(d["evidence"]) == 1

    def test_defaults(self):
        from src.core.capability_assessor import CapabilityGap
        gap = CapabilityGap()
        assert gap.name == ""
        assert gap.impact == 0.0
        assert gap.evidence == []


class TestProjectProposal:
    """Tests for ProjectProposal dataclass."""

    def test_to_dict(self):
        from src.core.capability_assessor import ProjectProposal
        p = ProjectProposal(
            gap_name="music_gen",
            title="Music Generation Pipeline",
            description="Build a Suno API wrapper",
            research_needed="Compare Suno vs Udio",
            estimated_phases=3,
            jesse_actions=["Sign up for Suno", "Add API key"],
            priority="high",
        )
        d = p.to_dict()
        assert d["title"] == "Music Generation Pipeline"
        assert d["estimated_phases"] == 3
        assert len(d["jesse_actions"]) == 2


# ── Persistence tests ────────────────────────────────────────────────


class TestPersistence:
    """Tests for assessment storage."""

    @patch("src.core.capability_assessor._base_path")
    def test_load_empty(self, mock_bp):
        from src.core.capability_assessor import _load_assessments
        with tempfile.TemporaryDirectory() as td:
            mock_bp.return_value = Path(td)
            data = _load_assessments()
            assert data["assessments"] == []
            assert data["last_assessed"] is None

    @patch("src.core.capability_assessor._base_path")
    def test_save_and_load_roundtrip(self, mock_bp):
        from src.core.capability_assessor import _load_assessments, _save_assessments
        with tempfile.TemporaryDirectory() as td:
            mock_bp.return_value = Path(td)
            data = {
                "assessments": [{"gaps": [], "timestamp": "2026-03-07"}],
                "last_assessed": "2026-03-07T12:00:00",
            }
            _save_assessments(data)
            loaded = _load_assessments()
            assert len(loaded["assessments"]) == 1
            assert loaded["last_assessed"] == "2026-03-07T12:00:00"

    @patch("src.core.capability_assessor._base_path")
    def test_save_trims_old_assessments(self, mock_bp):
        from src.core.capability_assessor import _save_assessments, _load_assessments, _MAX_ASSESSMENTS_STORED
        with tempfile.TemporaryDirectory() as td:
            mock_bp.return_value = Path(td)
            data = {
                "assessments": [{"i": i} for i in range(_MAX_ASSESSMENTS_STORED + 5)],
                "last_assessed": None,
            }
            _save_assessments(data)
            loaded = _load_assessments()
            assert len(loaded["assessments"]) == _MAX_ASSESSMENTS_STORED


# ── Cooldown tests ───────────────────────────────────────────────────


class TestCooldown:
    """Tests for assessment cooldown logic."""

    @patch("src.core.capability_assessor._load_assessments")
    def test_due_when_never_assessed(self, mock_load):
        from src.core.capability_assessor import is_assessment_due
        mock_load.return_value = {"assessments": [], "last_assessed": None}
        assert is_assessment_due()

    @patch("src.core.capability_assessor._load_assessments")
    def test_not_due_when_recent(self, mock_load):
        from src.core.capability_assessor import is_assessment_due
        mock_load.return_value = {
            "assessments": [],
            "last_assessed": datetime.now().isoformat(),
        }
        assert not is_assessment_due()

    @patch("src.core.capability_assessor._load_assessments")
    def test_due_when_old(self, mock_load):
        from src.core.capability_assessor import is_assessment_due, _COOLDOWN_HOURS
        old = (datetime.now() - timedelta(hours=_COOLDOWN_HOURS + 1)).isoformat()
        mock_load.return_value = {"assessments": [], "last_assessed": old}
        assert is_assessment_due()


# ── Evidence gathering tests ─────────────────────────────────────────


class TestEvidenceGathering:
    """Tests for individual evidence source collectors."""

    def test_gather_failed_tasks(self):
        from src.core.capability_assessor import _gather_failed_tasks
        from src.core.learning_system import Experience

        mock_ls = MagicMock()
        mock_ls.experiences = [
            Experience("failure", "goal context", "web_search", "timed out", "network unreliable"),
            Experience("success", "other context", "create_file", "created", None),
            Experience("failure", "music goal", "generate_music", "no tool", "missing capability"),
        ]
        evidence = _gather_failed_tasks(mock_ls)
        assert len(evidence) == 2  # Only failures
        assert "web_search" in evidence[0]
        assert "generate_music" in evidence[1]

    def test_gather_failed_tasks_none(self):
        from src.core.capability_assessor import _gather_failed_tasks
        evidence = _gather_failed_tasks(None)
        assert evidence == []

    @patch("src.core.capability_assessor.get_interests", create=True)
    @patch("src.core.capability_assessor.get_personal_projects", create=True)
    def test_gather_worldview_gaps(self, mock_projects, mock_interests):
        # Patch at the import level since the function does its own import
        with patch("src.core.worldview.get_interests") as mi, \
             patch("src.core.worldview.get_personal_projects") as mp:
            mi.return_value = [
                {"topic": "music production", "curiosity_level": 0.8, "notes": "want to make beats"},
            ]
            mp.return_value = [
                {"title": "AI Music Experiment", "work_sessions": 5, "status": "active"},
            ]
            from src.core.capability_assessor import _gather_worldview_gaps
            evidence = _gather_worldview_gaps()
            assert any("music production" in e for e in evidence)
            assert any("Stalled project" in e for e in evidence)

    def test_gather_tool_inventory(self):
        from src.core.capability_assessor import _gather_tool_inventory
        with patch("src.tools.tool_registry.get_shared_registry") as mock_reg:
            mock_registry = MagicMock()
            mock_registry.tools = {"web_search": None, "create_file": None, "generate_image": None}
            mock_registry._mcp_tools = set()
            mock_reg.return_value = mock_registry
            evidence = _gather_tool_inventory()
            assert len(evidence) >= 1
            assert "web_search" in evidence[0]

    def test_gather_avoidance_patterns(self):
        from src.core.capability_assessor import _gather_avoidance_patterns
        with patch("src.core.behavioral_rules.load") as mock_load:
            mock_load.return_value = {
                "avoidance": [
                    {"pattern": "long video generation", "reason": "always times out", "strength": 0.6},
                ],
                "preference": [],
            }
            evidence = _gather_avoidance_patterns()
            assert len(evidence) == 1
            assert "long video generation" in evidence[0]

    @patch.dict(os.environ, {
        "GITHUB_PAT": "test",
        "GITHUB_BLOG_REPO": "test/repo",
        "TWITTER_API_KEY": "",
    }, clear=False)
    def test_gather_content_capabilities(self):
        from src.core.capability_assessor import _gather_content_capabilities
        evidence = _gather_content_capabilities()
        assert any("GitHub Blog" in e for e in evidence)
        assert any("NOT configured" in e for e in evidence)

    @patch("src.core.capability_assessor._base_path")
    def test_gather_stalled_goals(self, mock_bp):
        from src.core.capability_assessor import _gather_stalled_goals
        with tempfile.TemporaryDirectory() as td:
            mock_bp.return_value = Path(td)
            goals_path = os.path.join(td, "data")
            os.makedirs(goals_path)
            with open(os.path.join(goals_path, "goals_state.json"), "w") as f:
                json.dump({"goals": [
                    {
                        "description": "Create a podcast",
                        "tasks": [
                            {"status": "FAILED"},
                            {"status": "FAILED"},
                            {"status": "COMPLETED"},
                        ],
                    },
                ]}, f)
            evidence = _gather_stalled_goals()
            assert len(evidence) == 1
            assert "2 failed" in evidence[0]

    @patch("src.core.capability_assessor._base_path")
    def test_gather_stalled_goals_no_file(self, mock_bp):
        from src.core.capability_assessor import _gather_stalled_goals
        with tempfile.TemporaryDirectory() as td:
            mock_bp.return_value = Path(td)
            evidence = _gather_stalled_goals()
            assert evidence == []

    def test_gather_all_evidence(self):
        from src.core.capability_assessor import gather_all_evidence
        with patch("src.core.capability_assessor._gather_failed_tasks") as m1, \
             patch("src.core.capability_assessor._gather_worldview_gaps") as m2, \
             patch("src.core.capability_assessor._gather_tool_inventory") as m3, \
             patch("src.core.capability_assessor._gather_avoidance_patterns") as m4, \
             patch("src.core.capability_assessor._gather_content_capabilities") as m5, \
             patch("src.core.capability_assessor._gather_stalled_goals") as m6:
            m1.return_value = ["fail1"]
            m2.return_value = ["interest1"]
            m3.return_value = ["tools: a, b"]
            m4.return_value = []
            m5.return_value = ["platform: x"]
            m6.return_value = []

            evidence = gather_all_evidence(learning_system=MagicMock())
            assert "failed_tasks" in evidence
            assert "worldview" in evidence
            assert "tools" in evidence
            assert len(evidence["failed_tasks"]) == 1


# ── Gap parsing tests ────────────────────────────────────────────────


class TestGapParsing:
    """Tests for parsing model responses into CapabilityGap objects."""

    def test_parse_valid_json(self):
        from src.core.capability_assessor import _parse_gaps
        raw = json.dumps({"gaps": [
            {
                "name": "music_gen",
                "description": "No music generation",
                "impact": 0.8,
                "category": "content",
                "requires_from_jesse": "Suno API key",
                "evidence_refs": ["High interest: music"],
            },
        ]})
        gaps = _parse_gaps(raw)
        assert len(gaps) == 1
        assert gaps[0].name == "music_gen"
        assert gaps[0].impact == 0.8

    def test_parse_json_embedded_in_text(self):
        from src.core.capability_assessor import _parse_gaps
        raw = 'Here is my analysis:\n```json\n{"gaps": [{"name": "test", "description": "d", "impact": 0.5, "category": "skill"}]}\n```'
        gaps = _parse_gaps(raw)
        assert len(gaps) == 1
        assert gaps[0].name == "test"

    def test_parse_no_json(self):
        from src.core.capability_assessor import _parse_gaps
        gaps = _parse_gaps("No JSON here at all")
        assert gaps == []

    def test_parse_empty_gaps(self):
        from src.core.capability_assessor import _parse_gaps
        gaps = _parse_gaps('{"gaps": []}')
        assert gaps == []

    def test_parse_clamps_impact(self):
        from src.core.capability_assessor import _parse_gaps
        raw = json.dumps({"gaps": [
            {"name": "a", "impact": 1.5, "category": "x"},
            {"name": "b", "impact": -0.3, "category": "y"},
        ]})
        gaps = _parse_gaps(raw)
        assert gaps[0].impact == 1.0
        assert gaps[1].impact == 0.0

    def test_parse_sorts_by_impact(self):
        from src.core.capability_assessor import _parse_gaps
        raw = json.dumps({"gaps": [
            {"name": "low", "impact": 0.3, "category": "x"},
            {"name": "high", "impact": 0.9, "category": "y"},
            {"name": "mid", "impact": 0.6, "category": "z"},
        ]})
        gaps = _parse_gaps(raw)
        assert gaps[0].name == "high"
        assert gaps[1].name == "mid"
        assert gaps[2].name == "low"

    def test_parse_caps_at_max_gaps(self):
        from src.core.capability_assessor import _parse_gaps, _MAX_GAPS
        raw = json.dumps({"gaps": [
            {"name": f"gap_{i}", "impact": 0.5, "category": "x"}
            for i in range(_MAX_GAPS + 3)
        ]})
        gaps = _parse_gaps(raw)
        assert len(gaps) == _MAX_GAPS


# ── Assess function tests ────────────────────────────────────────────


class TestAssess:
    """Tests for the main assess() function."""

    @pytest.mark.asyncio
    async def test_assess_with_model_response(self):
        from src.core.capability_assessor import assess

        mock_router = MagicMock()
        mock_router.chat.return_value = {
            "content": json.dumps({"gaps": [
                {"name": "music_gen", "description": "Need music", "impact": 0.7, "category": "content",
                 "requires_from_jesse": "API key", "evidence_refs": ["interest"]},
            ]}),
            "cost": 0.02,
        }

        with patch("src.core.capability_assessor.gather_all_evidence") as mock_gather, \
             patch("src.core.capability_assessor._save_assessments"):
            mock_gather.return_value = {"worldview": ["High interest: music"]}
            gaps = await assess(mock_router, learning_system=MagicMock())
            assert len(gaps) == 1
            assert gaps[0].name == "music_gen"
            mock_router.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_assess_empty_evidence(self):
        from src.core.capability_assessor import assess

        with patch("src.core.capability_assessor.gather_all_evidence") as mock_gather:
            mock_gather.return_value = {}
            gaps = await assess(MagicMock())
            assert gaps == []

    @pytest.mark.asyncio
    async def test_assess_model_error(self):
        from src.core.capability_assessor import assess

        mock_router = MagicMock()
        mock_router.chat.side_effect = RuntimeError("API down")

        with patch("src.core.capability_assessor.gather_all_evidence") as mock_gather:
            mock_gather.return_value = {"tools": ["tool: a"]}
            gaps = await assess(mock_router)
            assert gaps == []


# ── Propose project tests ────────────────────────────────────────────


class TestProposeProject:
    """Tests for project proposal generation."""

    @pytest.mark.asyncio
    async def test_propose_valid(self):
        from src.core.capability_assessor import propose_project, CapabilityGap

        gap = CapabilityGap(name="music_gen", description="Need music", impact=0.8, category="content")
        mock_router = MagicMock()
        mock_router.chat.return_value = {
            "content": json.dumps({
                "title": "Music Generation Pipeline",
                "description": "Build Suno wrapper",
                "research_needed": "Compare APIs",
                "estimated_phases": 3,
                "jesse_actions": ["Get Suno key"],
                "priority": "high",
            }),
        }
        proposal = await propose_project(gap, mock_router)
        assert proposal is not None
        assert proposal.title == "Music Generation Pipeline"
        assert proposal.estimated_phases == 3

    @pytest.mark.asyncio
    async def test_propose_model_error(self):
        from src.core.capability_assessor import propose_project, CapabilityGap

        gap = CapabilityGap(name="test", description="d", impact=0.5)
        mock_router = MagicMock()
        mock_router.chat.side_effect = RuntimeError("fail")
        proposal = await propose_project(gap, mock_router)
        assert proposal is None

    @pytest.mark.asyncio
    async def test_propose_clamps_phases(self):
        from src.core.capability_assessor import propose_project, CapabilityGap

        gap = CapabilityGap(name="big", description="huge project", impact=0.9)
        mock_router = MagicMock()
        mock_router.chat.return_value = {
            "content": json.dumps({
                "title": "Big Project",
                "description": "Lots of work",
                "research_needed": "",
                "estimated_phases": 100,
                "jesse_actions": [],
                "priority": "high",
            }),
        }
        proposal = await propose_project(gap, mock_router)
        assert proposal.estimated_phases == 5  # Clamped to max


# ── Formatting tests ─────────────────────────────────────────────────


class TestFormatting:
    """Tests for Discord message formatting."""

    def test_format_gap_only(self):
        from src.core.capability_assessor import format_gap_message, CapabilityGap
        gap = CapabilityGap(name="music_gen", description="Cannot generate music")
        msg = format_gap_message(gap)
        assert "music_gen" in msg
        assert "Cannot generate music" in msg

    def test_format_with_proposal(self):
        from src.core.capability_assessor import format_gap_message, CapabilityGap, ProjectProposal
        gap = CapabilityGap(name="music_gen", description="Cannot generate music")
        proposal = ProjectProposal(
            gap_name="music_gen",
            title="Music Pipeline",
            description="Build a Suno wrapper",
            jesse_actions=["Get API key", "Choose plan"],
        )
        msg = format_gap_message(gap, proposal)
        assert "Music Pipeline" in msg
        assert "Get API key" in msg
        assert "go for it" in msg.lower()


# ── Stats and recent gaps tests ──────────────────────────────────────


class TestStats:
    """Tests for get_recent_gaps and get_assessment_stats."""

    @patch("src.core.capability_assessor._load_assessments")
    def test_get_recent_gaps(self, mock_load):
        from src.core.capability_assessor import get_recent_gaps
        mock_load.return_value = {
            "assessments": [
                {"gaps": [{"name": "a"}, {"name": "b"}]},
            ],
            "last_assessed": "2026-03-07",
        }
        gaps = get_recent_gaps()
        assert len(gaps) == 2
        assert gaps[0]["name"] == "a"

    @patch("src.core.capability_assessor._load_assessments")
    def test_get_recent_gaps_empty(self, mock_load):
        from src.core.capability_assessor import get_recent_gaps
        mock_load.return_value = {"assessments": [], "last_assessed": None}
        assert get_recent_gaps() == []

    @patch("src.core.capability_assessor._load_assessments")
    def test_assessment_stats(self, mock_load):
        from src.core.capability_assessor import get_assessment_stats
        mock_load.return_value = {
            "assessments": [
                {"gaps": [{"name": "a"}]},
                {"gaps": [{"name": "b"}, {"name": "c"}]},
            ],
            "last_assessed": "2026-03-07T12:00:00",
        }
        stats = get_assessment_stats()
        assert stats["total_assessments"] == 2
        assert stats["latest_gaps"] == 2
        assert stats["last_assessed"] == "2026-03-07T12:00:00"


# ── Pending proposal tracking (session 238) ───────────────────────────


class TestPendingProposal:
    """Tests for the pending gap proposal tracking used by the approval flow."""

    def setup_method(self):
        from src.core.capability_assessor import clear_pending_proposal
        clear_pending_proposal()

    def test_set_and_get(self):
        from src.core.capability_assessor import (
            CapabilityGap, ProjectProposal,
            set_pending_proposal, get_pending_proposal,
        )
        gap = CapabilityGap(name="music", description="Need music gen", impact=0.8)
        proposal = ProjectProposal(gap_name="music", title="Music Gen Pipeline")
        set_pending_proposal(gap, proposal)
        result = get_pending_proposal()
        assert result is not None
        assert result[0].name == "music"
        assert result[1].title == "Music Gen Pipeline"

    def test_get_when_empty(self):
        from src.core.capability_assessor import get_pending_proposal
        assert get_pending_proposal() is None

    def test_clear(self):
        from src.core.capability_assessor import (
            CapabilityGap, ProjectProposal,
            set_pending_proposal, get_pending_proposal, clear_pending_proposal,
        )
        gap = CapabilityGap(name="test")
        proposal = ProjectProposal(title="Test")
        set_pending_proposal(gap, proposal)
        assert get_pending_proposal() is not None
        clear_pending_proposal()
        assert get_pending_proposal() is None

    def test_overwrite(self):
        from src.core.capability_assessor import (
            CapabilityGap, ProjectProposal,
            set_pending_proposal, get_pending_proposal,
        )
        set_pending_proposal(CapabilityGap(name="old"), ProjectProposal(title="Old"))
        set_pending_proposal(CapabilityGap(name="new"), ProjectProposal(title="New"))
        result = get_pending_proposal()
        assert result[0].name == "new"
        assert result[1].title == "New"
