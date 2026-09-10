---
id: P-011
title: AI and Machine Learning Model Governance Policy
owner: Head of Data Science
effective: 2025-03-10
last_reviewed: 2026-02-25
domain: technology-governance
---

## 1. Scope

### 1.1 Covered systems
This Policy applies to any statistical or machine learning model whose output influences a clinical, coverage, eligibility or employment decision.

### 1.2 Third-party models
Models obtained from third parties are within scope, and the absence of visibility into a vendor's training data does not remove the obligations in this Policy.

## 2. Approval

### 2.1 Registration
Each in-scope model shall be recorded in the Model Register before being used in production, with an identified accountable owner.

### 2.2 Intended use statement
Each registered model shall have a documented statement of intended use, and use outside that statement requires re-approval.

### 2.3 Pre-deployment evaluation
A model shall not be deployed to production until its performance has been evaluated on a dataset representative of the population on which it will be used.

## 3. Monitoring

### 3.1 Ongoing performance
Model performance shall be monitored in production and reviewed by the accountable owner at least quarterly.

### 3.2 Subgroup analysis
Performance shall be assessed across relevant demographic subgroups, and material disparities shall be escalated to the Head of Data Science.

### 3.3 Drift
Material change in input distribution shall trigger re-evaluation of the model within thirty (30) days.

## 4. Transparency and Records

### 4.1 Decision records
Where a model contributes to a decision affecting an individual, the inputs and output shall be recorded in accordance with the Audit Logging and Monitoring Policy.

### 4.2 Human oversight
An in-scope model shall not be the sole basis for a decision that materially affects an individual without documented human review.

### 4.3 Retention of model artifacts
Model versions, training configuration and evaluation results shall be retained for the operational life of the model.
