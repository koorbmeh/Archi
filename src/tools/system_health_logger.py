import os
from src.monitoring.system_monitor import SystemMonitor

class SystemHealthLogger:
    def __init__(self, log_dir='data/logs'):
        self.log_dir = log_dir
        os.makedirs(self.log_dir, exist_ok=True)

    def log_health_metrics(self):
        monitor = SystemMonitor()
        health_data = monitor.check_health()
        
        log_file_path = os.path.join(self.log_dir, 'system_health_log.txt')
        with open(log_file_path, 'a') as log_file:
            log_file.write(f"Date: {health_data.timestamp}\n")
            log_file.write(f"CPU: {health_data.cpu}%\n")
            log_file.write(f"Memory: {health_data.memory}%\n")
            log_file.write(f"Disk: {health_data.disk}%\n")
            log_file.write(f"Temperature: {health_data.temperature}\n\n")

        self._generate_summary_report(health_data)

    def _generate_summary_report(self, health_data):
        summary_file_path = os.path.join(self.log_dir, 'temperature_issue_report.txt')
        with open(summary_file_path, 'a') as summary_file:
            summary_file.write(f"\n\nTemperature Alert: {health_data.temperature}\n")
            summary_file.write(f"Timestamp: {health_data.timestamp}\n\n")
            summary_file.write(f"CPU: {health_data.cpu}% | Memory: {health_data.memory}% | Disk: {health_data.disk}%\n\n")

    def get_log_directory(self):
        return self.log_dir