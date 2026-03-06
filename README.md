# Glitter — Debate Domain Prototype

Architecture Prototype — Debate Domain Modeling Experiment

⚠️ Experimental Prototype — Domain Architecture Exploration

https://glitter.im

Glitter is an experimental **debate-domain prototype** exploring how structured discussion systems can be modeled as a reusable domain module.

This project is **not a finished discussion platform**.  
Instead, it focuses on the **domain architecture of structured debates**: claims, arguments, counterarguments, moderation workflows, and policy rules.

The goal is to explore whether discussion systems can have a **reusable debate-domain layer** similar to how other systems share domain models (for example carts in ecommerce).

---

# Motivation

Most online discussion systems are implemented as simple comment trees.

However, real debates often have a more explicit structure:

- a thesis or claim
- supporting arguments
- counterarguments
- moderation workflows
- reporting systems
- audit logs

These elements usually exist implicitly in community platforms but are rarely modeled as **first-class domain entities**.

This project experiments with treating structured debate as a domain model rather than just a UI pattern.

---

# Core Domain Model

The prototype models debate discussions with the following entities.

## Thesis
The main claim or topic being discussed.

## Argument
Supporting reasoning that strengthens the thesis.

## Counter
A counterargument attached to a specific argument.

## ContentReport
Reporting workflow used for moderation.

## UserRole
Role-based moderation and operational permissions.

## AuditLog
Event log for moderation and system actions.

The resulting structure resembles a small **discussion graph**, not a flat comment tree.

---

# Architecture

The codebase is organized around layered responsibilities.

## Domain Models


Thesis
Argument
Counter
ContentReport
UserRole
AuditLog


## Service Layer

Domain write operations:

- report submission
- moderation actions
- status transitions

## Query Layer

Reusable read models:

- thesis detail assembly
- moderation report views

## Policy Layer

Shared non-UI policy logic:

- moderation roles
- visibility rules
- status transitions

## Interface Layer

- server-rendered pages (Django templates)
- JSON API endpoints

One design goal was ensuring that **SSR pages and API endpoints reuse the same query layer**.

---

# Current Features

The prototype currently includes:

- structured debate entities
- argument and counterargument relationships
- moderation and reporting workflows
- audit logging
- role-based moderation
- query/service separation
- shared policy layer
- SSR + API reuse of read models

This repository represents an early **architecture prototype** rather than a finished product.

---

# Example Debate Structure

A typical discussion structure looks like this:


Thesis
├─ Argument A
│ ├─ Counter A1
│ └─ Counter A2
└─ Argument B
└─ Counter B1


Moderation flows operate on these entities through reports and status transitions.

---

# Technology Stack

Core stack:

- Python
- Django
- MariaDB / MySQL
- Gunicorn
- server-rendered templates
- small JSON API

The system intentionally avoids heavy frontend frameworks to keep the domain logic clear.

---

# Project Structure


app/
thinking/
models.py # domain entities
services/ # domain write operations
queries/ # reusable read models
policies.py # shared policy layer
views.py # SSR interface
templates/

api/
views.py # JSON endpoints

authflow/
email login + Google One Tap


---

# Running the Project

Clone the repository.


git clone <repo-url>
cd glitter


Create environment variables.


cp .env.example .env


Install dependencies.


pip install -r requirements.txt


Run migrations.


./run_manage.sh migrate


Start the server.


./run_gunicorn.sh


---

# Development

Run tests.


APP_ENV=test ./run_manage.sh test


Run checks.


./run_manage.sh check


CI script.


scripts/ci.sh


---

# Feedback

This project is an exploration of **structured debate domain modeling**.

Feedback is welcome, especially about:

- debate domain design
- moderation workflows
- service/query separation
- policy-layer architecture
- potential real-world use cases

---

# License

TBD
