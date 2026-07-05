# Contributing to AlphaLab

Thank you for your interest in contributing to AlphaLab.

AlphaLab is an open-source Python framework for deterministic quantitative research and algorithmic trading. The project is built around immutable state, event-driven architecture, strict static typing, and production-oriented engineering practices.

Whether you're fixing bugs, improving documentation, adding new functionality, or proposing new ideas, your contributions are welcome.

---

# Before You Begin

Before contributing, we recommend reading:

- `README.md`
- `docs/ARCHITECTURE.md`
- `docs/ENGINEERING_GUIDELINES.md`
- `ROADMAP.md`

Understanding the overall architecture before making changes will help keep the framework consistent and maintainable.

---

# Code of Conduct

Please read our `CODE_OF_CONDUCT.md` before contributing.

We are committed to maintaining a respectful, collaborative, and inclusive community for everyone.

---

# Development Setup

Clone the repository.

```bash
git clone https://github.com/VarunSingh022/AlphaLab.git

cd AlphaLab
```

Create a virtual environment.

```bash
python -m venv .venv
```

Activate the environment.

### Linux / macOS

```bash
source .venv/bin/activate
```

### Windows

```powershell
.venv\Scripts\activate
```

Install development dependencies.

```bash
pip install -e ".[dev]"
```

---

# Validate Your Environment

Run the complete validation suite before making changes.

```bash
ruff check .

mypy .

pytest
```

All three commands must complete successfully before code is submitted.

---

# Repository Structure

```
alphalab/      Framework source code

tests/         Unit tests

examples/      Runnable examples

docs/          Project documentation

benchmarks/    Performance benchmarks

configs/       Reference configuration files
```

Production code, tests, examples, and documentation should remain synchronized whenever public functionality changes.

---

# Development Workflow

1. Create a feature branch.

```bash
git checkout -b feature/my-feature
```

2. Implement your changes.

3. Add or update tests.

4. Update documentation if required.

5. Run the validation suite.

6. Commit your work.

7. Open a Pull Request.

---

# Coding Standards

AlphaLab emphasizes correctness, readability, and reproducibility over clever implementations.

Contributors should:

- Preserve immutable state
- Prefer deterministic behavior
- Write fully typed public APIs
- Follow existing module boundaries
- Avoid hidden side effects
- Keep functions focused and composable
- Maintain consistent naming and documentation

Every contribution must pass Ruff, MyPy, and the full test suite.

Detailed engineering standards are available in:

```
docs/ENGINEERING_GUIDELINES.md
```

---

# Testing

Every new feature should include appropriate automated tests.

Tests should verify:

- Expected behavior
- Edge cases
- Invalid inputs
- Error handling
- Deterministic execution

The test directory should mirror the production package whenever practical.

Example:

```
alphalab/research/

↓

tests/unit/research/
```

---

# Documentation

Documentation is considered part of the codebase.

Whenever public APIs or behavior change, update the relevant documentation.

This may include:

- README
- Examples
- Architecture Guide
- System Design
- Engineering Guidelines
- API documentation

Keeping documentation synchronized with implementation is a project requirement.

---

# Commit Messages

Write clear and descriptive commit messages.

Good examples:

```
Add Feature Store engine

Implement Polygon market data provider

Improve portfolio optimizer normalization

Refactor Strategy Studio validation
```

Avoid messages such as:

```
fix

update

misc

changes
```

---

# Pull Requests

Well-structured pull requests are easier to review and merge.

Please keep pull requests:

- Focused
- Well documented
- Fully tested
- Limited to a single logical change

---

# Pull Request Checklist

Before opening a pull request, verify that you have:

- [ ] Added or updated tests where appropriate
- [ ] Updated documentation if public behavior changed
- [ ] Run `ruff check .`
- [ ] Run `mypy .`
- [ ] Run `pytest`
- [ ] Confirmed all checks pass
- [ ] Reviewed your changes before submission

---

# Reporting Bugs

When reporting bugs, please include:

- Operating system
- Python version
- AlphaLab version
- Steps to reproduce
- Expected behavior
- Actual behavior
- Error messages
- Stack trace (if applicable)

Small reproducible examples are greatly appreciated.

---

# Feature Requests

Feature requests should clearly describe:

- The problem being solved
- Why it is valuable
- A proposed solution
- Alternative approaches (if applicable)

Discussion before implementation is encouraged for larger architectural changes.

---

# Good First Contributions

New contributors are encouraged to start with smaller improvements.

Examples include:

- Documentation improvements
- Additional examples
- Benchmark scenarios
- New market data providers
- Additional broker adapters
- Portfolio optimization algorithms
- Testing improvements
- Performance optimizations
- Bug fixes

If available, look for GitHub issues labeled:

- `good first issue`
- `help wanted`

---

# Getting Help

Questions are always welcome.

If you need assistance:

- Open a GitHub Discussion
- Open an Issue
- Create a Draft Pull Request

We're happy to help contributors get started.

---

# Licensing

By submitting a contribution, you agree that your work will be licensed under the project's MIT License.

---

# Thank You

Every contribution—whether it is a bug fix, documentation improvement, test enhancement, or major feature—helps make AlphaLab better.

Thank you for helping build a deterministic, well-tested, and production-quality quantitative research framework.