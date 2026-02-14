import os
import time
from datetime import datetime

class SystemMonitor:
    def __init__(self):
        self.disk_usage_log = []
        self.log_file_path = 'workspace/research/disk_usage_trend_summary.txt'

    def get_disk_usage_trend(self, days=7):
        
        # Get current disk usage
        total, used, free = self._get_disk_usage()
        current_usage_percent = (used / total) * 100 if total > 0 else 0
        
        # Log current usage
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.disk_usage_log.append((timestamp, current_usage_percent))
        
        # Save to log file
        with open(self.log_file_path, 'a') as f:
            f.write(f'{timestamp}, {current_usage_percent}\n')
        
        # Return the trend data
        return self.disk_usage_log

    def _get_disk_usage(self):
        # Get disk usage for the root partition (you can modify this for specific partitions)
        stat = os.statvfs('/')
        total = stat.f_blocks * stat.f_frsize
        used = (stat.f_blocks - stat.f_free) * stat.f_frsize
        free = stat.f_free * stat.f_frsize
        return total, used, free