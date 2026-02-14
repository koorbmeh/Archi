import os
import time
from datetime import datetime

class SystemMonitor:
    def __init__(self):
        self.disk_usage_log = []
        self.log_file_path = 'workspace/research/disk_usage_trend_summary.txt'

    def get_disk_usage_trend(self, days=7, interval=3600):
        start_time = time.time()
        end_time = start_time + duration

        while time.time() < end_time:
            current_usage = self._get_disk_usage()
            timestamp = datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
            self.disk_usage_log.append((timestamp, current_usage))
            time.sleep(interval)

    def _get_disk_usage(self):
        # For simplicity, this example returns a simulated disk usage value.
        # In a real implementation, you would use os.statvfs or similar to get actual disk usage.
        return round(75 + (time.time() % 3600) / 60, 1)  # Simulated usage between 75% and 78%

    def log_disk_usage(self):
        with open(self.log_file_path, 'a') as f:
            for timestamp, usage in self.disk_usage_log:
                f.write(f"{timestamp}, {usage}\n")
        self.disk_usage_log = []