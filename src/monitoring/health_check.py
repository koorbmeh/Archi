"""
Health Check System - Monitor overall system health.

Provides health checks for all components and overall
system status for monitoring and alerting.
Complements SystemMonitor (resource metrics) with component-level checks.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
from src.utils.paths import base_path as _base_path

logger = logging.getLogger(__name__)

# Health status levels (string constants to avoid conflict with system_monitor.HealthStatus)
STATUS_HEALTHY = "healthy"
STATUS_DEGRADED = "degraded"
STATUS_UNHEALTHY = "unhealthy"
STATUS_UNKNOWN = "unknown"



class HealthCheck:
    """
    System health monitoring.

    Checks health of all major components and provides
    overall system status.
    """

    def __init__(self) -> None:
        self.last_check: Optional[datetime] = None
        self.last_status: Optional[str] = None
        logger.info("Health check system initialized")

    def check_all(self) -> Dict[str, Any]:
        """Run all health checks."""
        self.last_check = datetime.now()

        checks = {
            "system": self._check_system_resources(),
            "models": self._check_models(),
            "cache": self._check_cache(),
            "storage": self._check_storage(),
            "monitoring": self._check_monitoring(),
        }

        overall = self._determine_overall_status(checks)
        self.last_status = overall

        return {
            "timestamp": self.last_check.isoformat(),
            "overall_status": overall,
            "checks": checks,
            "summary": self._create_summary(checks),
        }

    def _check_system_resources(self) -> Dict[str, Any]:
        """Check CPU, memory, disk health."""
        try:
            cpu_percent = psutil.cpu_percent(interval=0.5)
            memory = psutil.virtual_memory()
            memory_percent = memory.percent

            base = _base_path()
            root = os.path.splitdrive(base)[0] or "C:"
            if not root.endswith(os.sep):
                root = root + os.sep
            disk = psutil.disk_usage(root)
            disk_percent = disk.percent

            from src.utils.config import get_monitoring
            mon = get_monitoring()

            issues: List[str] = []
            if cpu_percent > mon["cpu_threshold"]:
                issues.append(f"CPU usage high: {cpu_percent:.1f}%")
            if memory_percent > mon["memory_threshold"]:
                issues.append(f"Memory usage high: {memory_percent:.1f}%")
            if disk_percent > mon["disk_threshold"]:
                issues.append(f"Disk usage high: {disk_percent:.1f}%")

            status = (
                STATUS_UNHEALTHY
                if len(issues) > 1
                else STATUS_DEGRADED
                if issues
                else STATUS_HEALTHY
            )

            return {
                "status": status,
                "cpu_percent": cpu_percent,
                "memory_percent": memory_percent,
                "disk_percent": disk_percent,
                "issues": issues,
            }

        except Exception as e:
            return {"status": STATUS_UNKNOWN, "error": str(e)}

    def _check_models(self) -> Dict[str, Any]:
        """Check AI model availability (API-only architecture)."""
        try:
            api_available = False
            issues: List[str] = []

            # OpenRouter API: check env; load .env if not set (scripts may not load it)
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                try:
                    from dotenv import load_dotenv

                    env_path = Path(_base_path()) / ".env"
                    if env_path.exists():
                        load_dotenv(env_path)
                        api_key = os.environ.get("OPENROUTER_API_KEY")
                        logger.info(
                            "Health check - Loaded .env from %s, OPENROUTER_API_KEY=%s",
                            env_path,
                            "set" if api_key else "not set",
                        )
                    else:
                        logger.info("Health check - No .env at %s", env_path)
                except ImportError:
                    logger.debug("Health check - dotenv not installed")
                except Exception as e:
                    logger.warning("Health check - Error loading .env: %s", e)

            api_available = bool(api_key)
            api_reachable = False
            if api_available:
                # Validate key by pinging the free model
                try:
                    from src.models.openrouter_client import OpenRouterClient
                    client = OpenRouterClient()
                    resp = client.generate("ping", model="openrouter/auto", max_tokens=1)
                    api_reachable = resp.get("success", False)
                    if not api_reachable:
                        issues.append(f"OpenRouter API unreachable: {resp.get('error', 'unknown')}")
                except Exception as e:
                    issues.append(f"OpenRouter connectivity check failed: {e}")
            else:
                issues.append("OpenRouter API key not configured")

            logger.info(
                "Health check - OpenRouter: key=%s, reachable=%s",
                "present" if api_available else "missing",
                api_reachable,
            )

            status = (
                STATUS_UNHEALTHY
                if not api_available
                else STATUS_HEALTHY if api_reachable
                else STATUS_DEGRADED
            )

            return {
                "status": status,
                "api_available": api_available,
                "api_reachable": api_reachable,
                "issues": issues,
            }

        except Exception as e:
            logger.error("Health check models error: %s", e, exc_info=True)
            return {"status": STATUS_UNKNOWN, "error": str(e)}

    def _check_cache(self) -> Dict[str, Any]:
        """Check cache system health."""
        try:
            from src.models.cache import QueryCache

            cache = QueryCache()
            stats = cache.get_stats()

            hit_rate = stats.get("hit_rate_percent", 0)
            size = stats.get("cached_entries", 0)
            max_size = getattr(cache, "_max_size", 0) or 1000

            issues: List[str] = []
            if hit_rate < 20 and (stats.get("total_queries", 0) or 0) > 10:
                issues.append(f"Low cache hit rate: {hit_rate:.1f}%")
            if max_size > 0 and size > max_size * 0.9:
                issues.append("Cache nearly full")

            status = STATUS_DEGRADED if issues else STATUS_HEALTHY

            return {
                "status": status,
                "hit_rate": hit_rate,
                "size": size,
                "max_size": max_size if max_size > 0 else "unbounded",
                "issues": issues,
            }

        except Exception as e:
            return {"status": STATUS_UNKNOWN, "error": str(e)}

    def _check_storage(self) -> Dict[str, Any]:
        """Check data storage health."""
        try:
            base = _base_path()
            data_dir = Path(base) / "data"

            issues: List[str] = []
            if not data_dir.exists():
                try:
                    data_dir.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    issues.append(f"Data directory missing: {e}")
                    return {
                        "status": STATUS_UNHEALTHY,
                        "data_dir_exists": False,
                        "missing_files": issues,
                        "issues": issues,
                    }

            # Optional files created on first use - not an issue if missing

            status = STATUS_DEGRADED if issues else STATUS_HEALTHY

            return {
                "status": status,
                "data_dir_exists": True,
                "issues": issues,
            }

        except Exception as e:
            return {"status": STATUS_UNKNOWN, "error": str(e)}

    def _check_monitoring(self) -> Dict[str, Any]:
        """Check monitoring systems (budget, etc.)."""
        try:
            from src.monitoring.cost_tracker import get_cost_tracker

            tracker = get_cost_tracker()
            budget = tracker.check_budget()

            issues: List[str] = []
            if not budget.get("allowed", True):
                issues.append(f"Budget exceeded: {budget.get('reason', 'unknown')}")

            daily_limit = budget.get("daily_limit", 1) or 1
            daily_spent = budget.get("daily_spent", 0)
            daily_pct = (daily_spent / daily_limit * 100) if daily_limit > 0 else 0

            from src.utils.config import get_monitoring
            _budget_warn_pct = get_monitoring()["budget_warning_pct"]
            if daily_pct > _budget_warn_pct:
                issues.append(f"Daily budget {daily_pct:.0f}% used")

            status = STATUS_DEGRADED if issues else STATUS_HEALTHY

            return {
                "status": status,
                "budget_allowed": budget.get("allowed", True),
                "daily_budget_pct": daily_pct,
                "issues": issues,
            }

        except Exception as e:
            return {"status": STATUS_UNKNOWN, "error": str(e)}

    def _determine_overall_status(self, checks: Dict[str, Dict[str, Any]]) -> str:
        """Determine overall system status from component checks."""
        statuses = [
            c.get("status", STATUS_UNKNOWN)
            for c in checks.values()
            if isinstance(c, dict)
        ]

        if STATUS_UNHEALTHY in statuses:
            return STATUS_UNHEALTHY
        if STATUS_DEGRADED in statuses:
            return STATUS_DEGRADED
        if STATUS_UNKNOWN in statuses:
            return STATUS_UNKNOWN
        return STATUS_HEALTHY

    def _create_summary(self, checks: Dict[str, Dict[str, Any]]) -> str:
        """Create human-readable summary with per-component detail."""
        lines: List[str] = []

        for component, check in checks.items():
            if not isinstance(check, dict):
                continue
            status = check.get("status", STATUS_UNKNOWN)
            comp_issues = check.get("issues", [])
            error = check.get("error")

            if status == STATUS_HEALTHY:
                lines.append(f"{component}: OK")
            elif comp_issues:
                lines.append(f"{component}: {status} — {'; '.join(comp_issues)}")
            elif error:
                lines.append(f"{component}: {status} — {error}")
            else:
                lines.append(f"{component}: {status}")

        if not lines:
            return "No checks ran"

        degraded = [l for l in lines if "OK" not in l]
        if not degraded:
            return "All systems operational (" + ", ".join(lines) + ")"
        return " | ".join(lines)

    def format_report(self) -> str:
        """Run all checks and return a concise human-readable report.

        Convenience method for notifications and PlanExecutor usage.
        Includes per-component status with specific metrics/issues.
        """
        result = self.check_all()
        overall = result["overall_status"]
        summary = result.get("summary", "")
        checks = result.get("checks", {})

        lines = [f"Overall: {overall.upper()}"]
        for comp, data in checks.items():
            if not isinstance(data, dict):
                continue
            status = data.get("status", "unknown")
            issues = data.get("issues", [])
            # Include key metrics inline where available
            extras = []
            if "cpu_percent" in data:
                extras.append(f"CPU {data['cpu_percent']:.0f}%")
            if "memory_percent" in data:
                extras.append(f"Mem {data['memory_percent']:.0f}%")
            if "disk_percent" in data:
                extras.append(f"Disk {data['disk_percent']:.0f}%")
            if "api_reachable" in data:
                extras.append(f"API {'reachable' if data['api_reachable'] else 'UNREACHABLE'}")
            if "daily_budget_pct" in data:
                extras.append(f"Budget {data['daily_budget_pct']:.0f}% used")
            if "hit_rate" in data:
                extras.append(f"Cache hit {data['hit_rate']:.0f}%")

            metric_str = f" ({', '.join(extras)})" if extras else ""
            issue_str = f" — {'; '.join(issues)}" if issues else ""
            lines.append(f"  {comp}: {status}{metric_str}{issue_str}")

        return "\n".join(lines)


# Global instance
health_check = HealthCheck()
