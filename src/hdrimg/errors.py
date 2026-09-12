class HdrImgError(RuntimeError):
    """Expected user-facing processing failure."""


class DependencyError(HdrImgError):
    """A required external program is unavailable."""


class InputError(HdrImgError):
    """An input or option is invalid."""


class ProcessingError(HdrImgError):
    """An external process or image operation failed."""
