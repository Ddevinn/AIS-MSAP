from __future__ import annotations

import pickle
import tempfile
import unittest
from pathlib import Path

import numpy as np

from backend.app.services.pickle_ingestion import load_pickle_trajectories


class _UnexpectedObject:
    def __init__(self, marker):
        self.marker = marker

    def __reduce__(self):
        return (eval, (f"__import__('pathlib').Path({str(self.marker)!r}).touch()",))


class PickleIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "trajectories.pkl"

    def test_numpy_protocols_and_legacy_module_names(self):
        for protocol in range(2, pickle.HIGHEST_PROTOCOL + 1):
            for dtype in (np.float64, np.float32, np.int64):
                with self.subTest(protocol=protocol, dtype=dtype):
                    expected = np.arange(18, dtype=dtype).reshape(2, 9)
                    self.source.write_bytes(pickle.dumps([expected], protocol=protocol))
                    np.testing.assert_array_equal(load_pickle_trajectories(self.source)[0], expected)
        # Existing files were written with NumPy 1.x's module namespace.
        payload = pickle.dumps([expected], protocol=3).replace(b"numpy._core.", b"numpy.core.")
        self.source.write_bytes(payload)
        np.testing.assert_array_equal(load_pickle_trajectories(self.source)[0], expected)

    def test_arbitrary_python_global_is_rejected_without_execution(self):
        marker = self.root / "must-not-exist"
        self.source.write_bytes(pickle.dumps([_UnexpectedObject(marker)]))
        with self.assertRaisesRegex(ValueError, "无法读取 PKL"):
            load_pickle_trajectories(self.source)
        self.assertFalse(marker.exists())

    def test_object_arrays_are_rejected(self):
        self.source.write_bytes(pickle.dumps([np.ones((1, 9), dtype=object)]))
        with self.assertRaisesRegex(ValueError, "非数值"):
            load_pickle_trajectories(self.source)


if __name__ == "__main__":
    unittest.main()
