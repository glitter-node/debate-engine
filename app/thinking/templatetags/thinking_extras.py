"""
app.thinking.templatetags.thinking_extras - Custom template filters for the "thinking" app.
"""

from django import template

from thinking.roles import user_has_site_role
from thinking.site_roles import normalize_role_name

register = template.Library()


@register.filter
def get_item(d, key):
    return d.get(key)


@register.simple_tag(takes_context=True)
def has_site_role(context, *roles):
    request = context.get("request")
    if request is None:
        return False
    normalized_roles = []
    for role in roles:
        try:
            normalized_roles.append(normalize_role_name(role))
        except ValueError:
            continue
    return user_has_site_role(request.user, *normalized_roles)
