# Monitoring and Alerting Strategy

## 1. Introduction

This document outlines the strategy for monitoring the DICOM Backup System and for alerting on critical issues that may arise in a production environment. The goal is to ensure system reliability, data integrity, and timely response to operational problems.

## 2. System Health Monitoring

### Scope

This involves monitoring the underlying infrastructure health for the key components of the backup system:

*   Main Orthanc Server (`orthanc-main`)
*   Staging SCP for Mosaiq (`orthanc-staging`)
*   Backup Application Host

### Key OS-level Metrics (for each host type)

The following operating system-level metrics should be collected and monitored for each host:

*   **CPU Utilization:**
    *   Average CPU utilization across all cores.
    *   Per-core utilization to identify imbalances.
    *   Alert on sustained high utilization (e.g., >80% for 15 minutes) that could indicate performance bottlenecks.
*   **Memory Usage:**
    *   Total memory used and available.
    *   Swap memory usage (should ideally be minimal).
    *   Alert on critically low available memory.
*   **Disk I/O:**
    *   Read/write latency (ms).
    *   Input/Output Operations Per Second (IOPS).
    *   Disk queue depth/length.
    *   Alert on consistently high latency or queue depths that indicate storage bottlenecks, especially for Orthanc storage volumes.
*   **Disk Space:**
    *   Percentage and absolute values of free disk space on all critical volumes:
        *   Orthanc storage volumes (main and staging).
        *   Application log directories.
        *   Operating system root/boot disks.
    *   Alert on disk space falling below predefined thresholds (e.g., warning at <20% free, critical at <10% free).
*   **Network I/O:**
    *   Bytes sent/received.
    *   Packets sent/received.
    *   Network interface error counts (e.g., dropped packets).
    *   TCP connection states (e.g., number of `ESTABLISHED`, `TIME_WAIT` connections).
    *   Alert on unusual network traffic patterns, high error rates, or excessive connections.
*   **Process Health:**
    *   Ensure critical processes are running:
        *   Orthanc server process on `orthanc-main` and `orthanc-staging`.
        *   The Python backup application script (when it's scheduled to run or if it's a long-running daemon in a future design).
    *   Alert if any of these key processes are not running.

### Tools

*   **Metrics Collection:** Conceptually, [Prometheus](https://prometheus.io/) with [Node Exporter](https://prometheus.io/docs/guides/node-exporter/) can be used to collect OS-level metrics from each host.
*   **Dashboards:** [Grafana](https://grafana.com/) can be used to create dashboards for visualizing these metrics, allowing for trend analysis and operational oversight.

## 3. Application-Level Monitoring

### Scope

This involves monitoring the performance, behavior, and specific operational metrics of the DICOM Backup application itself, including its interactions with source systems and backup targets.

### Key DICOM Operation Metrics

These metrics should be logged by the application and, where feasible, exposed for monitoring systems:

*   **Backup Jobs (Overall and Per Source - ARIA, MIM, Mosaiq):**
    *   Number of backup jobs started.
    *   Number of backup jobs completed successfully.
    *   Number of backup jobs failed.
    *   Duration of each backup job.
    *   Number of DICOM instances processed per job.
    *   Number of DICOM instances successfully backed up per job.
*   **C-FIND Operations (e.g., by `dicom_utils` or Orthanc verification):**
    *   Count of C-FIND requests sent (per peer AE).
    *   Success/failure rate of C-FIND requests (per peer AE), based on DICOM status.
    *   Average and percentile response times for C-FIND operations.
*   **C-MOVE Operations (e.g., by ARIA/MIM `transfer`, or `dicom_utils` for Mosaiq staging to main):**
    *   Count of C-MOVE requests initiated (per source AE to destination AE).
    *   Success/failure rate of C-MOVE operations (based on final DICOM status and reported sub-operations).
    *   Number of instances reported as successfully moved by the SCP.
    *   Transfer times for C-MOVE operations (if the protocol/tooling allows for easy measurement).
*   **C-STORE Operations (e.g., by Mosaiq `transfer` to staging SCP):**
    *   Count of C-STORE requests sent to the staging SCP.
    *   Success/failure rate of C-STORE requests (based on DICOM status).
*   **C-GET Operations (e.g., by `dicom_utils` for Orthanc verification):**
    *   Count of C-GET requests sent to the backup Orthanc server.
    *   Success/failure rate of C-GET operations.
    *   Number of instances successfully retrieved.
*   **Verification Status:**
    *   Number of instances successfully verified (found, retrieved, and matched).
    *   Number of instances that failed verification (not found, retrieval failed, or data mismatch).
*   **Queue Depths (Future Consideration):**
    *   If the application evolves to use internal queues for processing DICOM instances or jobs, the depth of these queues would be a critical metric. (Currently, the application is script-based and processes instances sequentially within a job).
*   **Error Rates:**
    *   General application error rates (e.g., Python exceptions).
    *   Count of unhandled exceptions.
    *   Specific DICOM error types encountered (e.g., association rejected, specific status codes).

### Implementation Methods

*   **Structured Logging:**
    *   The primary method for capturing these metrics will be through structured logging (e.g., JSON format). Each log entry related to these operations should include relevant metric data, timestamps, source system, and status.
    *   These logs should be shipped to a centralized log management system (e.g., ELK Stack - Elasticsearch, Logstash, Kibana; or Grafana Loki) for parsing, aggregation, and visualization.
*   **Metrics Endpoint (Conceptual - Future Enhancement):**
    *   For a more advanced setup, the Python application could be refactored (e.g., if it becomes a long-running service) to expose a metrics endpoint compatible with Prometheus (e.g., using the `prometheus_client` library). This would allow direct scraping of metrics by Prometheus.
*   **DICOM Service Logs:**
    *   The main Orthanc server and the staging SCP (if also Orthanc or another DICOM server) will produce their own detailed operational logs. These logs are invaluable for diagnosing DICOM communication issues and should also be collected, centralized, and monitored.

## 4. Alerting

### Scope

Alerting is crucial for notifying operations personnel of critical issues that require immediate attention to maintain system availability and data integrity.

### Critical Alert Conditions

Alerts should be configured for, but not limited to, the following conditions:

*   **System Down:**
    *   Any of the key hosts (Main Orthanc Server, Staging SCP, Backup Application Host) are unreachable (e.g., ping failure, SSH failure, Node Exporter down).
*   **Process Failure:**
    *   The Orthanc server process is not running on `orthanc-main` or `orthanc-staging`.
    *   The backup application script fails to start or exits prematurely with an error during a scheduled run.
*   **Disk Space Critical:**
    *   Free disk space on Orthanc storage volumes (main and staging) falls below a critical threshold (e.g., <10% remaining).
    *   Free disk space on application log directories or the OS disk of any host falls below a critical threshold.
*   **Backup Job Failures:**
    *   Consistent or repeated failure of backup jobs for any specific source system (ARIA, MIM, Mosaiq).
    *   A significant drop in the number of instances backed up compared to historical averages for a given job.
*   **High DICOM Operation Failure Rate:**
    *   A significant increase in the failure rate of C-FIND, C-MOVE, C-GET, or C-STORE operations to/from any configured DICOM peer.
    *   Specific DICOM error statuses that indicate persistent problems (e.g., `Refused: Out of Resources`, `Processing Failure`).
*   **Prolonged Backup Durations:**
    *   Backup jobs taking significantly longer to complete than their established baseline duration (e.g., >2x average).
*   **Application Errors:**
    *   A high rate of unhandled Python exceptions or critical errors logged by the backup application.
    *   Specific error messages known to indicate critical problems.
*   **DICOM Connectivity Failure:**
    *   Failure to establish DICOM associations with any configured source AE, the main Orthanc backup server, or the Mosaiq staging SCP. This could be due to network issues, incorrect AE configurations, or the peer AE being down.

### Alerting Tools

*   **Prometheus + Alertmanager:** If Prometheus is used for metrics collection, [Alertmanager](https://prometheus.io/docs/alerting/latest/alertmanager/) can be used to define alerting rules based on these metrics and manage alert notifications.
*   **Centralized Logging System Alerts:** Modern centralized logging systems (like ELK with ElastAlert, or Grafana Loki with its alerting features) can also be configured to trigger alerts based on log patterns, error counts, or aggregated metrics derived from logs.

### Notification Channels

Alerts should be routed to appropriate personnel or teams through various channels, such as:

*   **PagerDuty (or similar):** For critical alerts requiring immediate, on-call response.
*   **Slack/Microsoft Teams:** For high-priority alerts that need quick visibility by the operations team.
*   **Email:** For lower-priority alerts, warnings, or daily summaries.

The choice and configuration of notification channels should align with the organization's incident response procedures.
