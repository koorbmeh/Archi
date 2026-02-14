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
            log_file.write(f"Date: {health_data.date}\n")
            log_file.write(f"CPU: {health_data.cpu}%\n")
            log_file.write(f"Memory: {health_data.memory}%\n")
            log_file.write(f"Disk: {health_data.disk}%\n")
            log_file.write(f"Temperature: {health_data.temperature}°C\n\n")

        self._generate_summary_report()

    def _generate_summary_report(self):
        summary_file_path = os.path.join(self.log_dir, 'temperature_issue_report.txt')
        with open(summary_file_path, 'a') as summary_file:
            summary_file.write(f"\n\nTemperature Summary Report\n")
            summary_file.write(f"Date: {self._get_current_date()}\n")
            summary_file.write(f"Highest Temperature: {self._get_highest_temperature()}°C\n")
            summary_file.write(f"Average Temperature: {self._get_average_temperature()}°C\n\n")

    def _get_current_date(self):
        from datetime import datetime
        return datetime.now().strftime('%Y-%m-%d')

    def _get_highest_temperature(self):
        # Placeholder logic - replace with actual data retrieval
        return 55  # Example value

    def _get_average_temperature(self):
        # Placeholder logic - replace with actual data retrieval
        return 45  # Example value