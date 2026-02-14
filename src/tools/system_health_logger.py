import os
from src.monitoring.system_monitor import SystemMonitor

class SystemHealthLogger:
    def __init__(self, log_dir='data/logs'):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def log_health_metrics(self):
        monitor = SystemMonitor()
        health = monitor.check_health()
        log_file = os.path.join(self.log_dir, 'system_health_log.txt')
        with open(log_file, 'a') as f:
            f.write(f"Date: {health.timestamp}\n")
            f.write(f"CPU: {health.cpu}%, Mem: {health.memory}%, Disk: {health.disk}%, Temp: {health.temperature}\n\n")
        print(f"Logged system health metrics to {log_file}")

    def generate_summary(self):
        summary_file = os.path.join(self.log_dir, 'system_health_summary.txt')
        with open(summary_file, 'w') as f:
            f.write("System Health Summary\n")
            f.write(f"Generated on: {datetime.datetime.now()}\n\n")
            f.write("Please review the detailed log file for comprehensive metrics.")
        print(f"Generated summary report to {summary_file}")