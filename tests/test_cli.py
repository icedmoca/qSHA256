"""The CLI: every subcommand runs, and none of them overstate what they did."""

from __future__ import annotations

import json

import pytest

from qsha256.cli import main


def run(capsys, *argv, expect: int = 0) -> tuple[str, str]:
    """Invoke the CLI and return ``(stdout, stderr)``.

    The exit status is asserted rather than returned, so no test can quietly
    ignore a command that failed.
    """
    code = main(list(argv))
    captured = capsys.readouterr()
    assert code == expect, f"{' '.join(argv)} exited {code}, expected {expect}"
    return captured.out, captured.err


class TestBasics:
    def test_version(self, capsys):
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0

    def test_no_command_exits(self):
        with pytest.raises(SystemExit):
            main([])

    def test_unknown_spec_is_rejected(self):
        with pytest.raises(SystemExit):
            main(["analyze", "--spec", "blake3"])

    def test_sha512_is_a_known_spec(self, capsys):
        """It was a rejection case until SHA-512 was actually implemented."""
        out, _ = run(capsys, "analyze", "--spec", "sha512", "--rounds", "2", "--format", "json")
        import json

        assert json.loads(out)["spec_name"] == "sha512"


class TestAnalyze:
    def test_text_report(self, capsys):
        out, _ = run(capsys, "analyze", "--spec", "toy4", "--rounds", "4")
        assert "qSHA256 - Quantum Resource Analysis" in out
        assert "[MEASURED]" in out
        assert "Assumptions" in out
        assert "Run on hardware:      no" in out

    def test_json_is_machine_readable(self, capsys):
        out, _ = run(capsys, "analyze", "--spec", "toy4", "--rounds", "4", "--format", "json")
        data = json.loads(out)
        assert data["spec_name"] == "toy4"
        assert data["hardware_executed"] is False
        assert data["environment"]["qiskit_version"]

    @pytest.mark.parametrize("fmt", ["text", "json", "csv", "markdown"])
    def test_all_formats(self, capsys, fmt):
        out, _ = run(capsys, "analyze", "--spec", "toy4", "--rounds", "2", "--format", fmt)
        assert out.strip()

    def test_writes_to_a_file(self, capsys, tmp_path):
        path = tmp_path / "sub" / "report.json"
        _, _ = run(
            capsys,
            "analyze",
            "--spec",
            "toy4",
            "--rounds",
            "2",
            "--format",
            "json",
            "--output",
            str(path),
        )
        assert json.loads(path.read_text())["spec_name"] == "toy4"

    @pytest.mark.parametrize("model", ["standard", "selinger", "jones"])
    def test_toffoli_model_changes_the_answer(self, capsys, model):
        out, _ = run(
            capsys,
            "analyze",
            "--spec",
            "toy4",
            "--rounds",
            "2",
            "--format",
            "json",
            "--toffoli-model",
            model,
        )
        assert json.loads(out)["clifford_t"]["model"] == model

    def test_rewrite_flag_is_recorded(self, capsys):
        out, _ = run(capsys, "analyze", "--spec", "toy4", "--rounds", "4", "--rewrite")
        assert "rewriting applied" in out

    def test_preset_overrides_flags(self, capsys):
        out, _ = run(
            capsys,
            "analyze",
            "--spec",
            "toy4",
            "--rounds",
            "4",
            "--preset",
            "min-qubits",
            "--format",
            "json",
        )
        assert json.loads(out)["strategy"]["const_add"] == "vbe_const"


class TestCircuit:
    def test_summary_goes_to_stderr(self, capsys):
        _, err = run(capsys, "circuit", "--spec", "toy4", "--rounds", "1")
        assert "qubits:" in err and "gates:" in err

    def test_draw_small_circuit(self, capsys):
        # toy4 at 1 round is still 514 gates, so the guard should refuse.
        run(capsys, "circuit", "--spec", "toy4", "--rounds", "1", "--draw", expect=1)

    def test_refuses_to_draw_a_huge_circuit(self, capsys):
        _, err = run(capsys, "circuit", "--rounds", "64", "--draw", expect=1)
        assert "Refusing to draw" in err

    def test_qasm_export(self, capsys, tmp_path):
        path = tmp_path / "c.qasm"
        _, _ = run(capsys, "circuit", "--spec", "toy4", "--rounds", "1", "--qasm", str(path))
        assert path.read_text().startswith("OPENQASM 3")


class TestOtherCommands:
    def test_validate_quick_passes(self, capsys):
        run(capsys, "validate", "--quick")

    def test_benchmark_markdown_table(self, capsys):
        out, _ = run(capsys, "benchmark", "--spec", "toy4", "--rounds", "1,2")
        assert out.startswith("| Circuit |")
        assert out.count("\n") >= 4

    def test_search_json(self, capsys):
        out, _ = run(
            capsys,
            "search",
            "--spec",
            "toy4",
            "--rounds",
            "2",
            "--format",
            "json",
            "--no-rewrite",
            "--no-verify",
        )
        data = json.loads(out)
        assert data["spec"] == "toy4"
        assert data["pareto_front"]

    def test_physical_states_assumptions(self, capsys):
        out, _ = run(capsys, "physical", "--spec", "toy4", "--rounds", "4")
        assert "ASSUMPTION-DEPENDENT" in out
        assert "Surface code" in out
        assert "No hardware was involved" in out

    def test_physical_accepts_several_models(self, capsys):
        out, _ = run(
            capsys,
            "physical",
            "--spec",
            "toy4",
            "--rounds",
            "2",
            "--model",
            "optimistic",
            "conservative",
        )
        assert out.count("Fault-Tolerant Resource Estimate") == 2

    def test_leaderboard_cites_its_sources(self, capsys):
        out, _ = run(capsys, "leaderboard", "--spec", "toy4", "--rounds", "8")
        assert "arXiv:1603.09383" in out
        assert "Comparability notes" in out

    def test_oracle_reports_grover_extrapolation(self, capsys):
        out, _ = run(capsys, "oracle", "--spec", "toy4", "--rounds", "4", "--search-bits", "64")
        assert "[MEASURED from a constructed circuit]" in out
        assert "[EXTRAPOLATED" in out
        assert "MAXDEPTH" in out

    def test_grover_demo_runs_and_passes(self, capsys):
        out, _ = run(capsys, "grover-demo")
        assert "REDUCED TOY HASH, not SHA-256" in out
        assert "PASS" in out
