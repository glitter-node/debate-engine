from django.db import transaction


def create_thesis_with_arguments(*, form, argument_formset, author):
    """Create thesis + non-empty arguments preserving previous behavior."""
    with transaction.atomic():
        thesis = form.save(commit=False)
        thesis.author = author
        thesis.save()
        argument_formset.instance = thesis
        arguments = argument_formset.save(commit=False)
        cleaned = [a for a in arguments if (a.body or "").strip()]
        if not cleaned:
            thesis.delete()
            return None
        for argument in cleaned:
            argument.thesis = thesis
            argument.save()
    return thesis
