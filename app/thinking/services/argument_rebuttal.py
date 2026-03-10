from ..domain.chain_validator import validate_argument_belongs_to_thesis


def create_counter_for_thesis(*, form, thesis, author, parent_counter=None):
    """Create counter within constrained chain policy.

    `parent_counter` is optional; when provided this creates a rebuttal node.
    """
    target_argument = form.cleaned_data.get("target_argument")
    if parent_counter is not None:
        validate_argument_belongs_to_thesis(
            thesis_id=thesis.id,
            argument_thesis_id=parent_counter.thesis_id,
        )
        target_argument = parent_counter.target_argument
    validate_argument_belongs_to_thesis(
        thesis_id=thesis.id,
        argument_thesis_id=target_argument.thesis_id,
    )

    counter = form.save(commit=False)
    counter.thesis = thesis
    counter.target_argument = target_argument
    counter.parent_counter = parent_counter
    counter.author = author
    counter.save()
    return counter
