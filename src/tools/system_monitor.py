import os
import time
from datetime import datetime

class SystemMonitor:
    def __init__(self):
        self.disk_usage_log = []
        self.log_file_path = 'workspace/research/disk_usage_trend_summary.txt'

    def get_disk_usage_trend(self, interval=3600, duration=24*3600):
        start_time = time.time()
        end_time = start_time + duration

        while time.time() < end_time:
            current_usage = self._get_disk_usage()
            timestamp = datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d %H:%M:%S')
            self.disk_usage_log.append((timestamp, current_usage))
            self._log_disk_usage(timestamp, current_usage)
            time.sleep(interval)

    def _get_disk_usage(self):
        # Example: Get disk usage for the root partition
        import shutil
        total, used, free = shutil.disk_usage('/')
        percent_used = (used / total) * 100
        return percent_used

    def _log_disk_usage(self, timestamp, usage):
        with open(self.log_file_path, 'a') as f:
            f.write(f'{timestamp}, {usage:.2f}%\n')

    def log_metrics(self):
        # This method can be used to save metrics to a database or file
        pass