import os
import time
from datetime import datetime


class SystemMonitor:
    def __init__(self):
        self.disk_usage_log = []
        self.log_file_path = 'workspace/research/disk_usage_trend_summary.txt'

    def get_disk_usage_trend(self, days=7, interval=3600):
        """Track disk usage over time at regular intervals.

        Args:
            days: Number of days to monitor.
            interval: Seconds between samples (default 1 hour).

        Returns:
            List of (timestamp, usage_percent) tuples.
        """
        start_time = time.time()
        end_time = start_time + days * 86400

        while time.time() < end_time:
            current_usage = self._get_disk_usage()
            timestamp = datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
            self.disk_usage_log.append((timestamp, current_usage))
            time.sleep(interval)

        return self.disk_usage_log

    def _get_disk_usage(self):
        """Get current disk usage percentage.

        Returns actual disk usage via os.statvfs (Unix) or shutil (cross-platform).
        """
        try:
            import shutil
            total, used, free = shutil.disk_usage(os.path.splitdrive(os.getcwd())[0] or "/")
            return round((used / total) * 100, 1)
        except Exception:
            return 0.0

    def log_disk_usage(self):
        """Append collected disk usage data to log file and clear the buffer."""
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        with open(self.log_file_path, 'a') as f:
            for timestamp, usage in self.disk_usage_log:
                f.write(f"{timestamp}, {usage}\n")
        self.disk_usage_log = []
