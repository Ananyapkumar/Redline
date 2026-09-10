---
id: P-004
title: Audit Logging and Monitoring Policy
owner: Director of Security Operations
effective: 2023-09-01
last_reviewed: 2025-04-08
domain: security
---

## 1. Scope

### 1.1 Covered systems
This Policy applies to all production systems that create, receive, maintain or transmit protected health information.

## 2. Events to be Logged

### 2.1 Access events
Systems shall record each access to protected health information, including the identity of the user, the record accessed, the action performed, and the date and time.

### 2.2 Authentication events
Successful and failed authentication attempts shall be recorded, including source network address.

### 2.3 Administrative events
Changes to user privileges, security configuration and audit settings shall be recorded.

### 2.4 Automated decision events
Where an automated system contributes to a clinical or coverage determination, the system shall record the inputs relied upon and the output produced.

## 3. Log Retention

### 3.1 Retention period
Audit logs shall be retained in a readily retrievable form for a period of not less than twelve (12) months.

### 3.2 Archival
Logs older than twelve (12) months may be moved to archival storage and need not remain immediately queryable.

## 4. Monitoring and Review

### 4.1 Review cadence
Security Operations shall review audit log exception reports not less than weekly.

### 4.2 Alerting
Anomalous access patterns shall generate an alert to Security Operations within one (1) hour of detection.

### 4.3 Integrity
Audit logs shall be protected against modification and deletion, including by privileged users.

## 5. Availability to Regulators

### 5.1 Production of logs
Audit logs shall be made available to a competent authority upon lawful request.
