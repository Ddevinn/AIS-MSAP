"""Read the numeric trajectory subset of pickle without arbitrary Python globals."""
from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
from numpy._core.multiarray import _reconstruct, scalar
from numpy._core.numeric import _frombuffer

from backend.app.config import AIS_COLUMNS


def _latin1_bytes(value, encoding, errors="strict"):
    # NumPy's protocol-2 pickles encode raw array bytes this way. Do not expose
    # the general codec registry to uploaded files.
    if encoding != "latin1" or errors != "strict":
        raise pickle.UnpicklingError("不支持的 PKL 字节编码。")
    return value.encode("latin1")


class _TrajectoryUnpickler(pickle.Unpickler):
    def find_class(self, module, name):
        if module == "numpy" and name in {"ndarray", "dtype"}:
            return {"ndarray": np.ndarray, "dtype": np.dtype}[name]
        if module in {"numpy.core.multiarray", "numpy._core.multiarray"}:
            if name in {"_reconstruct", "scalar"}:
                return {"_reconstruct": _reconstruct, "scalar": scalar}[name]
        if module in {"numpy.core.numeric", "numpy._core.numeric"} and name == "_frombuffer":
            return _frombuffer
        if (module, name) == ("_codecs", "encode"):
            return _latin1_bytes
        raise pickle.UnpicklingError("PKL 包含不支持的对象，仅支持数值轨迹数据。")


def load_pickle_trajectories(source: Path) -> list[np.ndarray]:
    try:
        with source.open("rb") as handle:
            trajectories = _TrajectoryUnpickler(handle, encoding="latin1").load()
    except (pickle.UnpicklingError, EOFError, ValueError, TypeError, AttributeError) as exc:
        raise ValueError("无法读取 PKL 文件，仅支持由数值轨迹数组组成的列表。") from exc
    if not isinstance(trajectories, list) or not trajectories:
        raise ValueError("PKL 应为非空的轨迹数组列表。")
    rows = 0
    for index, trajectory in enumerate(trajectories):
        array = np.asarray(trajectory)
        if array.ndim != 2 or array.shape[1] != len(AIS_COLUMNS):
            raise ValueError(f"第 {index + 1} 条轨迹应为 N×{len(AIS_COLUMNS)} 的数值数组。")
        if array.dtype.kind not in "biuf":
            raise ValueError(f"第 {index + 1} 条轨迹包含非数值数据。")
        trajectories[index] = array
        rows += len(array)
    if not rows:
        raise ValueError("PKL 文件没有轨迹点。")
    return trajectories
