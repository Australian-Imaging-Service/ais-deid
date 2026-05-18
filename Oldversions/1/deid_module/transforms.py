import hashlib

SALT = "CHANGE_ME_TO_SECURE_RANDOM_SALT"

def _hash(value: str) -> str:
    return hashlib.sha256((SALT + value).encode()).hexdigest()[:16]


def hash_id(item, value, field, dicom):
    """
    Deterministic anonymised identifier.
    Keeps consistency across studies.
    """
    if not value:
        return value
    return _hash(value)


def passthrough(item, value, field, dicom):
    return value
