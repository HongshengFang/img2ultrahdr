class HdrImgError(RuntimeError):
    """Expected user-facing processing failure."""


class DependencyError(HdrImgError):
    """A required external program is unavailable."""


class InputError(HdrImgError):
    """An input or option is invalid."""


class ProcessingError(HdrImgError):
    """An external process or image operation failed."""


class DiskSpaceError(ProcessingError):
    """There is insufficient working space for an expensive image operation."""
