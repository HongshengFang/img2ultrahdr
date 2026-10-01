"""Optional app-bundled accelerator; the CLI retains its NumPy reference path."""
import ctypes
import os
from functools import lru_cache
import numpy as np

@lru_cache(maxsize=1)
def library():
    path=os.environ.get('HDRIMG_ACCELERATOR')
    if not path: return None
    try:
        lib=ctypes.CDLL(path)
        f=lib.hdr_envelopes
        f.argtypes=[ctypes.c_void_p]*3+[ctypes.c_int,ctypes.c_int,ctypes.c_double,
                                      ctypes.POINTER(ctypes.c_int),ctypes.POINTER(ctypes.c_double)]
        f.restype=ctypes.c_int
        lib.hdr_matmul.argtypes=[ctypes.c_void_p]*3+[ctypes.c_size_t,ctypes.c_int]
        lib.hdr_matmul.restype=None
        return lib
    except (OSError,AttributeError):
        return None

def envelopes(guide,lower,upper,slope):
    lib=library()
    if lib is None: return None
    guide=np.ascontiguousarray(guide,dtype=np.float32)
    count=ctypes.c_int();change=ctypes.c_double()
    status=lib.hdr_envelopes(guide.ctypes.data,lower.ctypes.data,upper.ctypes.data,
        guide.shape[0],guide.shape[1],slope,ctypes.byref(count),ctypes.byref(change))
    if status != 0: raise MemoryError('HDR envelope allocation failed')
    return count.value-1,change.value

def matmul(rgb, matrix):
    lib=library()
    if lib is None or rgb.ndim < 2 or not matrix_kernel_compatible():
        return None
    values=np.ascontiguousarray(rgb,dtype=np.float32)
    coefficients=np.ascontiguousarray(matrix,dtype=np.float32)
    output=np.empty(values.shape,dtype=np.float32)
    lib.hdr_matmul(values.ctypes.data,coefficients.ctypes.data,output.ctypes.data,values.size//3,1)
    # Accelerate uses a scalar tail kernel for incomplete four-pixel tiles;
    # let NumPy reproduce that kernel's rounding rather than approximating it.
    remainder = values.shape[-2] % 4
    if remainder:
        output[..., -remainder:, :] = values[..., -remainder:, :] @ coefficients.T
    return output


@lru_cache(maxsize=1)
def matrix_kernel_compatible():
    """Do not assume another NumPy/BLAS build has Accelerate's rounding."""
    lib = library()
    if lib is None:
        return False
    values = (np.arange(24, dtype=np.float32).reshape(8, 3) / np.float32(17)) - np.float32(.4)
    matrix = np.array([[1.973, -.47, 1.106], [-.737, 2.73, .059], [.093, .217, .789]], dtype=np.float32)
    output = np.empty_like(values)
    lib.hdr_matmul(values.ctypes.data, matrix.ctypes.data, output.ctypes.data, 8, 1)
    return bool(np.array_equal(output, values @ matrix.T))
