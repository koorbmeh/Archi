import subprocess
import json

def run_scanner():
    """
    Run findings_scanner.py via subprocess, capture JSON stdout as list of findings dicts.
    Each finding dict has a 'value' field.
    """
    result = subprocess.run(
        ['python', 'findings_scanner.py'],
        capture_output=True,
        text=True,
        timeout=30  # 30 second timeout
    )
    if result.returncode != 0:
        raise RuntimeError(f"Scanner failed with return code {result.returncode}: {result.stderr}")
    try:
        findings = json.loads(result.stdout.strip())
        if not isinstance(findings, list):
            raise ValueError("Expected list of findings")
        for finding in findings:
            if not isinstance(finding, dict) or 'value' not in finding:
                raise ValueError("Each finding must be a dict with 'value' field")
        return findings
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Invalid JSON from scanner: {e}")
