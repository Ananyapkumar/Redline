---
id: P-003
title: Access Control Policy
owner: Chief Information Security Officer
effective: 2024-01-15
last_reviewed: 2026-01-20
domain: security
---

## 1. Principles

### 1.1 Least privilege
Access to Kestrel systems shall be granted on the principle of least privilege, limited to the minimum necessary to perform an assigned role.

### 1.2 Role-based assignment
Access shall be assigned by documented role rather than to individuals, and role definitions shall be maintained by the system owner.

## 2. Provisioning and Deprovisioning

### 2.1 Authorisation
Access requests shall be approved by the requester's manager and by the owner of the system concerned.

### 2.2 Termination
Access shall be revoked within twenty-four (24) hours of a workforce member's separation or role change.

### 2.3 Contractor access
Contractor accounts shall be assigned a fixed expiry date not exceeding twelve (12) months and shall not be renewed without re-approval.

## 3. Access Review

### 3.1 Review cadence
Access rights to systems containing protected health information shall be reviewed no less than quarterly by the system owner.

### 3.2 Privileged accounts
Accounts with administrative privilege shall be reviewed monthly and shall not be shared between individuals.

### 3.3 Evidence
Each access review shall produce a dated record identifying the reviewer, the accounts reviewed, and any access removed.

## 4. Authentication

### 4.1 Multi-factor authentication
Multi-factor authentication is required for all remote access and for all administrative access to production systems.

### 4.2 Service accounts
Non-human service accounts shall use credentials stored in the approved secrets manager and shall be rotated at least every ninety (90) days.

### 4.3 Emergency access
Break-glass credentials shall be sealed, logged on use, and rotated within twenty-four (24) hours of any use.
