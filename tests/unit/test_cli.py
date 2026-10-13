"""Unit tests for honba_examples.cli."""

from __future__ import annotations

import json
from pathlib import Path

from honba_examples.cli import add_out_arg, emit_json, make_parser, run_example


def test_make_parser_uses_first_paragraph() -> None:
    parser = make_parser("Title line.\n\nSecond paragraph.")
    assert parser.description == "Title line."


def test_make_parser_handles_none() -> None:
    assert make_parser(None).description == ""


def test_make_parser_adds_only_help() -> None:
    parser = make_parser("x")
    assert [a.dest for a in parser._actions] == ["help"]


def test_add_out_arg() -> None:
    parser = make_parser("x")
    add_out_arg(parser)
    assert parser.parse_args([]).out is None
    assert parser.parse_args(["--out", "a/b.json"]).out == Path("a/b.json")


def test_emit_json_prints(capsys) -> None:
    emit_json({"a": 1}, None)
    assert capsys.readouterr().out == json.dumps({"a": 1}, indent=2) + "\n"


def test_emit_json_writes_file_with_parents(tmp_path, capsys) -> None:
    out = tmp_path / "deep" / "r.json"
    emit_json({"a": object.__name__}, out)
    text = json.dumps({"a": "object"}, indent=2)
    assert out.read_text() == text + "\n"
    assert capsys.readouterr().out == text + "\n"


def test_emit_json_default_str(capsys) -> None:
    emit_json({"p": Path("x")}, None)
    assert '"x"' in capsys.readouterr().out


def test_run_example_passes_kwargs_without_out(capsys, tmp_path) -> None:
    seen = {}

    def run(n: int) -> dict:
        seen["n"] = n
        return {"n": n}

    def add_args(p) -> None:
        p.add_argument("--n", type=int, default=1)

    out = tmp_path / "o.json"
    rc = run_example(run, "Doc.", add_args, argv=["--n", "3", "--out", str(out)])
    assert rc == 0
    assert seen == {"n": 3}
    assert json.loads(out.read_text()) == {"n": 3}
    assert json.loads(capsys.readouterr().out) == {"n": 3}


def test_run_example_to_kwargs(capsys) -> None:
    def run(total: int) -> dict:
        return {"total": total}

    def add_args(p) -> None:
        p.add_argument("--a", type=int, default=1)

    rc = run_example(run, None, add_args, argv=["--a", "4"], to_kwargs=lambda a: {"total": a.a * 2})
    assert rc == 0
    assert json.loads(capsys.readouterr().out) == {"total": 8}


def test_run_example_without_add_args(capsys) -> None:
    assert run_example(lambda: {"ok": True}, "d", argv=[]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}
