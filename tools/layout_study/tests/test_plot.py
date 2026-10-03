"""Plotting rejection/interval semantics only; no synthetic scientific figures."""
import copy
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location("layout_plot", Path(__file__).resolve().parents[1] / "plot.py")
plot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(plot)


def record(point=1.0, bounds=None):
    return {"estimate": point, "percentile_interval": [0.5, 1.5] if bounds is None else bounds,
            "nominal_individual_coverage": .95, "invalid_bootstrap_replicates": 0}


class PlotValidationTests(unittest.TestCase):
    def test_interval_may_exclude_its_point_estimate(self):
        self.assertEqual(plot.interval(record(1.0, [2.0, 3.0]), .95, "interval"), (1.0, 2.0, 3.0))
        self.assertEqual(plot.interval(record(-2.0, [-3.0, -1.0]), .95, "signed"), (-2.0, -3.0, -1.0))

    def test_invalid_bounds_coverage_and_replications_rejected(self):
        for field, value in (("percentile_interval", [2,1]), ("percentile_interval", [0,float("inf")]),
                             ("percentile_interval", None), ("estimate", float("nan")),
                             ("nominal_individual_coverage", .99), ("invalid_bootstrap_replicates", 1)):
            metric=record();metric[field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):
                plot.interval(metric,.95,"interval")
        with self.assertRaises(ValueError): plot.interval(record(-1,[-2,0]),.95,"count",nonnegative=True)
        with self.assertRaises(ValueError): plot.interval(record(.8,[.5,1.1]),.95,"fraction",fraction=True)

    def test_actual_endpoints_are_drawn_without_negative_error_lengths(self):
        class Axis:
            def __init__(self): self.calls=[]
            def hlines(self,*args,**kwargs): self.calls.append(("h",args))
            def vlines(self,*args,**kwargs): self.calls.append(("v",args))
            def plot(self,*args,**kwargs): self.calls.append(("p",args))
        axis=Axis();plot.draw_intervals(axis,[4],[record(1,[2,3])],plot.STYLE["back-four"])
        self.assertEqual(axis.calls[0],("v",([4],[2],[3])))
        self.assertEqual(axis.calls[-1],("p",([4],[1])))
        axis=Axis();plot.draw_intervals(axis,[0],[record(-2,[-3,-1])],plot.STYLE["back-two"],horizontal=True)
        self.assertEqual(axis.calls[0],("h",([0],[-3],[-1])))

    def test_incomplete_or_nonfinite_result_is_rejected(self):
        for result in ({},{"schema_version":"steel-layout-first-scan-result-v1","status":"partial"},
                       {"schema_version":"steel-layout-first-scan-result-v1","status":"complete-fixed-first-sample","events":100}):
            with self.assertRaises(ValueError): plot.validate_result(result)
        with self.assertRaises(ValueError): plot.reject_nonfinite({"nested":[float("nan")]})

    def test_hash_is_checked_before_data_and_output_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/"result.json";source.write_text("{}")
            with self.assertRaisesRegex(ValueError,"SHA-256 mismatch"):
                plot.export(source,"0"*64,root/"figures")
            self.assertFalse((root/"figures").exists())
            with self.assertRaisesRegex(ValueError,"completed first-scan"):
                plot.export(source,plot.sha256(source),root/"figures")
            self.assertFalse((root/"figures").exists())
            (root/"figures").mkdir()
            with self.assertRaisesRegex(ValueError,"overwrite"):
                plot.export(source,plot.sha256(source),root/"figures")


if __name__ == "__main__":
    unittest.main()
