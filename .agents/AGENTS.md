# `honba-examples` Agent Guidelines & Architecture Invariants

**Subsystem**: Examples (Numbered Learning Path)
**Language**: Python
**Primary Responsibility**: Canonical tutorials and runnable reference scripts for Indian equities, options, and factor models.

---

## 1. Role & Architectural Hints
* `honba-examples` provides the official educational journey:
  - Step-by-step numbered scripts demonstrating data loading, indicator creation, order lifecycle, bracket orders, partial fills, and multi-symbol portfolios.
  - Every example script must remain runnable and covered by a corresponding test in `tests/unit/`.
* **Idea Inheritance**:
  - **Jesse**: Minimal boilerplate, clean logging, intuitive code paths for new quants.

---

## 2. Coding Guidelines (TDD)
* **Parallel Test Directory Rule (MANDATORY)**:
  - Every `examples/strategies/<num>_<name>.py` must have an automated regression test in `tests/unit/test_<num>_<name>.py`.
* **Zero Floats Policy**: Exact decimal math in all examples.

---

## 3. Development & CI Workflow
* **Linting & Formatting**:
  ```bash
  ruff check .
  ruff format --check .
  ```
* **Testing**:
  ```bash
  pytest -q
  ```
