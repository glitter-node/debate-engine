# Debate Engine - Glitter.im

**Debate Domain Prototype for Structured Debate Engine**

Project site: https://glitter.im

Glitter is a prototype exploring **debate-domain modeling** for structured discussion systems.

Instead of representing discussions as flat comment threads, this project models debates as **explicit domain entities with moderation and policy logic built into the model**.

This repository is **not intended to be a finished discussion platform**. It is primarily a **domain-modeling experiment** around structured debate systems.

---

## Why This Exists

Most discussion platforms implement conversations as simple comment trees.

However, real discussions usually have structure:

- a central claim
- supporting arguments
- counterarguments
- moderation workflows
- reporting and review
- audit trails

These structures often exist implicitly but are rarely modeled explicitly.

Glitter explores what happens when **discussion systems treat debate structure as a first-class domain model**.

---

## Core Domain Model

The prototype models discussions using several primary entities.

```text
Thesis
├─ Argument
│  └─ Counter
├─ Argument
│  └─ Counter
└─ ...
```

### Entities

| Entity | Description |
| --- | --- |
| **Thesis** | A central claim or topic under discussion |
| **Argument** | Supporting reasoning attached to a thesis |
| **Counter** | Counterarguments targeting specific arguments |
| **ContentReport** | Moderation workflow for problematic content |
| **UserRole** | Role system for moderators and operators |
| **AuditLog** | Audit trail for moderation and system events |

The goal is to represent **debate structure rather than a flat comment tree**.

---

## Architecture

The project is gradually evolving toward a reusable debate-domain module.

```text
thinking/
├─ models.py
│  Domain entities
│
├─ services/
│  Domain write orchestration
│  ├─ reporting.py
│  └─ moderation.py
│
├─ queries/
│  Reusable read models
│  ├─ thesis_detail.py
│  ├─ moderation.py
│  └─ reporting.py
│
├─ policies.py
│  Shared domain policy
│  ├─ moderation roles
│  ├─ visibility rules
│  └─ status transitions
│
└─ interfaces/
   ├─ views.py
   └─ api_views.py
```

### Design Goals

- **Domain-driven discussion model**
- **Reusable debate-domain module**
- **Service/query separation**
- **Shared policy layer**
- **SSR and API reuse of the same read models**

---

## Example Debate Structure

Traditional forum:

```text
Post
└─ Comment
   └─ Reply
```

Structured debate model:

```text
Thesis
├─ Argument
│  └─ Counter
├─ Argument
│  └─ Counter
└─ ...
```

This structure allows clearer reasoning chains and moderation workflows.

---

## Current Features

The prototype currently demonstrates:

- structured debate entities
- argument/counterargument relationships
- moderation workflows
- reporting system
- audit logging
- role-based moderation
- service/query separation
- shared policy layer
- SSR + API reuse of read models

The system supports both:

- server-rendered pages
- JSON API endpoints

---

## Development

The project uses **Django with MariaDB/MySQL by default**.

Typical development commands:

```bash
./run_manage.sh check
APP_ENV=test ./run_manage.sh test
make test
make lint
make ci
```

The test suite currently includes:

- integration tests
- policy-layer tests

---

## Project Status

This repository is an early prototype.

It demonstrates:

- debate-domain modeling
- moderation/report workflows
- architectural separation (services / queries / policies)

It does not aim to be a production-ready platform.

---

## Intended Direction

The longer-term direction is to explore whether the debate model could become a reusable component for systems such as:

- AI-assisted discussion platforms
- knowledge review tools
- community forums
- research discussion environments
- decision review systems

Rather than building a single platform, the project experiments with a debate-domain engine that other systems could embed.

---

## Feedback

Feedback is welcome, especially regarding:

- debate-domain modeling
- moderation/report architecture
- service/query separation
- policy-layer design
- possible real-world use cases

---

## Links

**Website**  
https://glitter.im

**Repository**  
https://github.com/glitter-node/structured-debate

---

## License

TBD

