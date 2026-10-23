import uuid


def new_review_id() -> str:
    return f"rev_{uuid.uuid4().hex[:12]}"
