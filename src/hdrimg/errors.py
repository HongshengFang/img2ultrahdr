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


class LocalSelectionError(ProcessingError):
    """A persisted, active selection cannot be used safely."""
    def __init__(self, region_id, detail):
        self.region_id = region_id
        super().__init__(f'Local region {region_id} needs a new selection: {detail}')
