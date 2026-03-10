def thesis_summary(thesis):
    return {
        "id": thesis.id,
        "title": thesis.title,
        "stance": thesis.stance,
        "author": thesis.author.username,
        "created_at": thesis.created_at.isoformat(),
        "updated_at": thesis.updated_at.isoformat(),
    }
