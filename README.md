```markdown
A prototype exploring debate-domain modeling for structured discussion systems.

# Glitter — Debate Domain Prototype

https://glitter.im

A prototype exploring **debate-domain modeling for structured discussion systems**.

Glitter experiments with representing discussions as structured debate entities rather than flat comment threads.

This repository is **not intended to be a finished discussion platform**.  
It is primarily a **domain modeling experiment** around structured debate systems.

---

# Motivation

Most online discussions are implemented as simple comment threads.

However many real discussions have implicit structure:

- a central claim
- supporting arguments
- counter arguments
- moderation workflows
- reporting and review
- audit trails

These elements usually exist in community platforms but are rarely modeled explicitly in the domain.

This project explores whether discussion systems could benefit from a **structured debate model**.

---

# Core Domain Model

The current prototype models discussions using several primary entities.


Thesis
Argument
Counter
ContentReport
UserRole
AuditLog


### Thesis

A central claim or topic under discussion.

### Argument

Supporting reasoning attached to a thesis.

### Counter

Counterarguments that target specific arguments.

### ContentReport

A moderation workflow for reporting problematic content.

### UserRole

Role system supporting moderators and operators.

### AuditLog

Audit trail for moderation actions and system events.

The goal is to represent debate as a structured system rather than a flat comment tree.

---

# Architecture

The codebase is organized to gradually evolve toward a reusable debate-domain module.


Domain Layer
models.py
Thesis
Argument
Counter
ContentReport
UserRole
AuditLog

Service Layer
services/
reporting.py
moderation.py

Query Layer
queries/
thesis_detail.py
moderation.py
reporting.py

Policy Layer
policies.py
moderation roles
visibility rules
status transitions

Interface Layer
views.py
api/views.py


One design goal is ensuring that **SSR pages and API endpoints reuse the same query layer**.

---

# Current Features

The prototype currently demonstrates:

- structured debate entities
- argument / counterargument relationships
- moderation workflows
- reporting system
- audit logging
- role-based moderation
- service / query separation
- shared policy layer
- SSR + API reuse of read models

The system supports both:

- server-rendered pages
- JSON API endpoints

using the same internal read models.

---

# Example Debate Structure

Instead of a comment tree:


Post
└─ Comment
└─ Reply


This project models discussions more like:


Thesis
├─ Argument
│ └─ Counter
├─ Argument
│ └─ Counter


This allows clearer reasoning structures and moderation workflows.

---

# Development

The project uses Django with MariaDB/MySQL by default.

Typical development commands:

```bash
./run_manage.sh check
APP_ENV=test ./run_manage.sh test
make test
make lint
make ci

The test suite includes integration tests and direct policy-layer tests.

Project Status

This is an early prototype.

The repository mainly demonstrates:

debate domain modeling

moderation / report workflows

architecture separation (service / query / policy)

It does not aim to be a production-ready platform.

Intended Direction

The long-term direction is exploring whether the debate model could become a reusable component for systems such as:

AI-assisted discussion platforms

knowledge review tools

community forums

research discussion environments

decision review systems

Rather than building a single platform, the project experiments with a debate-domain engine that other systems could embed.

Feedback

Feedback is welcome, especially regarding:

debate domain modeling

moderation/report architecture

query/service separation

policy-layer design

possible real-world use cases

Project

Website
https://glitter.im

Source code
(GitHub repository)

License

(To be determined)
