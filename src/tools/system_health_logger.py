from src.monitoring.system_monitor import SystemMonitor
import datetime

class SystemHealthLogger:
    def __init__(self, log_file='system_health_log.txt'):
        self.log_file = log_file
        self.system_monitor = SystemMonitor()

    def log_health_metrics(self):
        health_data = self.system_monitor.check_health()
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        with open(self.log_file, 'a') as f:
            f.write(f"[{timestamp}] CPU: {health_data.cpu}%, Memory: {health_data.memory}%, Disk: {health_data.disk}%, Temperature: {health_data.temperature}°C\n")

    def generate_summary_report(self):
        health_data = self.system_monitor.check_health()
        timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        summary = f"System Health Summary - {timestamp}\n"
        summary += f"CPU: {health_data.cpu}%\n"
        summary += f"Memory: {health_data.memory}%\n"
        summary += f"Disk: {health_data.disk}%\n"
        summary += f"Temperature: {health_data.temperature}°C\n"

        with open('temperature_issue_report.txt', 'a') as f:
            f.write(summary + '\n\n')

    def log_metrics(self):
        self.log_health_metrics()
        self.generate_summary_report()