import json
import unittest
from pathlib import Path
from typing import ClassVar

from evals.adapters.agenttune import from_eventlog, to_eventlog
from evals.metrics import (
    episode_latency,
    percentile,
    task_outcome,
    tool_call_accuracy,
    validate_call,
)
from evals.normalize import normalize_text, normalize_value, simplified_chars
from evals.run import evaluate
from evals.schema import load_tools, read_jsonl, validate_all

ROOT = Path(__file__).resolve().parents[2]
SAMPLES = ROOT / "data" / "samples"


def call(name, **args):
    return {"name": name, "arguments": args}


class NormalizeTest(unittest.TestCase):
    def test_fullwidth_variant_and_space(self):
        self.assertEqual(normalize_text("　臺北市　ＡＢＣ１２３ "), "台北市 abc123")

    def test_dates(self):
        self.assertEqual(normalize_text("民國115年11月16日"), "2026-11-16")
        self.assertEqual(normalize_text("中華民國 115 年 1 月 2 日"), "2026-01-02")
        self.assertEqual(normalize_text("2026/11/20"), "2026-11-20")
        self.assertEqual(normalize_text("2026 年 11 月 15 日"), "2026-11-15")

    def test_numbers(self):
        self.assertEqual(normalize_value("3,000"), 3000)
        self.assertEqual(normalize_value(3000.0), 3000)
        self.assertIs(normalize_value(True), True)

    def test_simplified_leak(self):
        self.assertEqual(simplified_chars("新台币约可换日圆"), ["圆", "币"])
        self.assertEqual(simplified_chars("新臺幣約可換日圓"), [])


class ToolCallAccuracyTest(unittest.TestCase):
    def test_modes(self):
        ref = [call("get_weather", city="台北市", date="2026-11-15")]
        pred = [call("get_weather", city="臺北市", date="2026-11-15")]
        self.assertEqual(tool_call_accuracy(pred, ref, "exact")["f1"], 0.0)
        self.assertEqual(tool_call_accuracy(pred, ref, "zh_norm")["f1"], 1.0)
        self.assertEqual(tool_call_accuracy(pred, ref, "name")["f1"], 1.0)

    def test_duplicates_and_order(self):
        a, b = call("a", x=1), call("b", y=2)
        r = tool_call_accuracy([b, a, a], [a, b])
        self.assertAlmostEqual(r["precision"], 2 / 3)
        self.assertEqual(r["recall"], 1.0)
        self.assertEqual(r["exact_set"], 0.0)
        r = tool_call_accuracy([b, a], [a, b])
        self.assertEqual((r["exact_set"], r["exact_sequence"]), (1.0, 0.0))

    def test_empty_reference(self):
        self.assertEqual(tool_call_accuracy([], [])["f1"], 1.0)
        self.assertEqual(tool_call_accuracy([call("a")], [])["f1"], 0.0)

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            tool_call_accuracy([], [], "fuzzy")


class SchemaValidityTest(unittest.TestCase):
    tools = load_tools(SAMPLES / "tools.json")

    def test_valid(self):
        self.assertEqual(
            validate_call(call("convert_currency", amount=3000, from_currency="TWD", to_currency="JPY"), self.tools), []
        )

    def test_errors(self):
        errs = validate_call(call("convert_currency", amount="3,000", from_currency="NTD", extra=1), self.tools)
        self.assertIn("missing required 'to_currency'", errs)
        self.assertIn("'amount' expected number", errs)
        self.assertIn("'from_currency' not in enum", errs)
        self.assertIn("undeclared argument 'extra'", errs)
        self.assertEqual(validate_call(call("nope"), self.tools), ["unknown tool 'nope'"])
        self.assertIn(
            "'start_time' does not match pattern",
            validate_call(
                call("create_calendar_event", title="x", date="2026-11-20", start_time="下午3點"), self.tools
            ),
        )


class OutcomeAndLatencyTest(unittest.TestCase):
    task: ClassVar[dict] = {
        "lang": "zh-Hant",
        "reference": {"answer": {"contains": ["14100"]}, "final_state": {"n": 1}},
    }

    def test_outcomes(self):
        ok = {"status": "completed", "final_answer": "約 14,100 日圓", "final_state": {"n": 1}}
        self.assertEqual(task_outcome(ok, self.task)["verdict"], "success")
        self.assertEqual(task_outcome({**ok, "final_state": {"n": 2}}, self.task)["verdict"], "failure")
        self.assertEqual(task_outcome({**ok, "status": "timeout"}, self.task)["verdict"], "error")
        self.assertEqual(task_outcome(None, self.task)["verdict"], "error")
        self.assertEqual(task_outcome(ok, {"reference": {}})["verdict"], "unknown")

    def test_latency(self):
        events = [
            {"type": "reasoning", "t_start_ms": 0, "t_end_ms": 100},
            {"type": "tool_call", "t_start_ms": 100, "t_end_ms": 150},
            {"type": "tool_result", "t_start_ms": 150, "t_end_ms": 400},
            {"type": "final", "t_start_ms": 400, "t_end_ms": 500},
        ]
        self.assertEqual(
            episode_latency({"events": events}),
            {"total_ms": 500, "model_ms": 250, "tool_ms": 250, "first_tool_call_ms": 150},
        )

    def test_percentile_nearest_rank(self):
        xs = [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]
        self.assertEqual(percentile(xs, 50), 50.0)
        self.assertEqual(percentile(xs, 95), 100.0)
        self.assertIsNone(percentile([], 50))


class SamplesEndToEndTest(unittest.TestCase):
    def setUp(self):
        self.tasks = read_jsonl(SAMPLES / "tasks.jsonl")
        self.tools = load_tools(SAMPLES / "tools.json")
        self.runs = read_jsonl(SAMPLES / "runs" / "demo-handwritten.jsonl")

    def test_samples_validate(self):
        self.assertEqual(validate_all(self.tasks, self.tools, self.runs), [])

    def test_demo_numbers(self):
        r = evaluate(self.tasks, self.tools, self.runs)["runs"]["demo-handwritten"]["by_lang"]
        self.assertEqual(r["en"]["tool_call"]["exact"]["f1"], 1.0)
        self.assertAlmostEqual(r["zh-Hant"]["tool_call"]["exact"]["f1"], 1 / 6)
        self.assertAlmostEqual(r["zh-Hant"]["tool_call"]["zh_norm"]["f1"], 11 / 12)
        self.assertEqual(r["zh-Hant"]["task_success"]["rate_all"], 1.0)
        self.assertEqual(r["zh-Hant"]["simplified_leak_rate"], 0.25)

    def test_deterministic(self):
        a = json.dumps(evaluate(self.tasks, self.tools, self.runs), sort_keys=True)
        b = json.dumps(evaluate(self.tasks, self.tools, list(reversed(self.runs))), sort_keys=True)
        self.assertEqual(a, b)

    def test_eventlog_round_trip_keeps_calls(self):
        for traj in self.runs:
            back = from_eventlog(to_eventlog(traj), run_id=traj["run_id"], task_id=traj["task_id"], model_id="x")
            self.assertEqual(
                [e for e in back["events"] if e["type"] == "tool_call"],
                [
                    {k: e[k] for k in ("type", "call_id", "name", "arguments", "t_start_ms", "t_end_ms")}
                    for e in traj["events"]
                    if e["type"] == "tool_call"
                ],
            )


@unittest.skipUnless(__import__("importlib").util.find_spec("auditkit"), "auditkit not installed")
class AuditKitCrossCheckTest(unittest.TestCase):
    def test_xcheck(self):
        from evals.adapters.auditkit_xcheck import xcheck

        problems = xcheck(
            read_jsonl(SAMPLES / "tasks.jsonl"),
            load_tools(SAMPLES / "tools.json"),
            read_jsonl(SAMPLES / "runs" / "demo-handwritten.jsonl"),
        )
        self.assertEqual(problems, [])


if __name__ == "__main__":
    unittest.main()
