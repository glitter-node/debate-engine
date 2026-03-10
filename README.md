# Debate Engine - Glitter.im

**Debate Domain Prototype**

This project explores **debate-domain modeling** for structured discussion systems.

Instead of storing discussions as chronological comment threads,
debates are modeled as **explicit argument structures**.

This repository is **not intended to be a finished discussion platform**,
but a prototype for experimenting with **structured debate models**.

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

Glitter experiments with treating debate structure as **a first-class domain model**.

---

## Core Domain Model

The prototype represents discussions using several primary entities.

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

The debate domain is implemented as a modular component within the project.

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

Create a virtual environment and install dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Environment configuration

Environment variables are loaded from the file specified by `APP_ENV_FILE`.

`/volume1/hwi/config/env/DjangoProto8/.env`

This path is specific to the production server and **does not need to be used in development**.

For local development:

```bash
cp .env.example .env
export APP_ENV_FILE=.env
```

Run database migrations:

```bash
./run_manage.sh migrate
```

Start the application server:

```bash
./run_gunicorn.sh
```

---

## Tests

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

## Resources

**Website**  
[glitter.im](https://djangoproto8.glitter.im)

**Repository**  
[Debate Engine](https://github.com/glitter-node/debate-engine)

---

## License

TBD

