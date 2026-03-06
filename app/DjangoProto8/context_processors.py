from __future__ import annotations

import data.config._conf_ as const


def global_template_context(_request):
    return dict(getattr(const, "TEMPLATES_CONTEXT", {}))
