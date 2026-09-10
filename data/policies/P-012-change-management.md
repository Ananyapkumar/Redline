---
id: P-012
title: Change Management and Release Control Policy
owner: VP Engineering
effective: 2023-04-03
last_reviewed: 2025-10-17
domain: technology-governance
---

## 1. Scope

### 1.1 Covered changes
This Policy applies to all changes to production systems, including configuration changes, infrastructure changes and code releases.

## 2. Authorisation

### 2.1 Change record
Every production change shall have a change record identifying the requester, the approver, the systems affected and the rollback plan.

### 2.2 Approval
Changes to systems processing PHI shall be approved by the system owner prior to deployment.

### 2.3 Emergency changes
Emergency changes may be deployed before approval, but a change record shall be completed within one (1) business day of deployment.

## 3. Testing

### 3.1 Pre-production validation
Changes shall be validated in a non-production environment before release, and evidence of validation shall be attached to the change record.

### 3.2 Production data in testing
Production PHI shall not be used in non-production environments unless de-identified in accordance with the Protected Health Information Handling Policy.

## 4. Deployment

### 4.1 Segregation of duties
The individual who authored a change shall not be the sole approver of its deployment to production.

### 4.2 Rollback
Each change record shall document a tested rollback procedure or a documented justification for its absence.

### 4.3 Post-deployment verification
Deployment shall be verified within one (1) hour of release and the result recorded.

## 5. Records

### 5.1 Change record retention
Change records shall be retained for three (3) years from the date of deployment.
