# Contributing to dbwarden

Thank you for your interest in contributing to dbwarden! This guide outlines the process for contributing to the project.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Workflow](#development-workflow)
- [Submitting Changes](#submitting-changes)
- [Coding Standards](#coding-standards)
- [Testing Requirements](#testing-requirements)
- [Documentation](#documentation)
- [Code of Conduct](#code-of-conduct)

## Getting Started

### Prerequisites

- Python 3.10 or higher
- Git
- A code editor of your choice

### Setting Up Development Environment

1. Fork the repository on GitHub.

2. Clone your fork locally:

```bash
git clone https://github.com/YOUR-USERNAME/dbwarden.git
cd dbwarden
```

3. Create a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

4. Install development dependencies:

```bash
pip install -e ".[dev]"
```

5. Install pre-commit hooks:

```bash
pre-commit install
```

## Development Workflow

### Branch Naming

- **Features**: `feature/short-description`
- **Bug Fixes**: `fix/short-description`
- **Hotfixes**: `hotfix/short-description`
- **Documentation**: `docs/short-description`
- **Refactors**: `refactor/short-description`

Example:
```
feature/add-migration-dependencies
fix/resolve-sqlite-foreign-key-issue
refactor/extract-snapshot-utils
```

### Creating Changes

1. Create a new branch from `main`:

```bash
git checkout main
git pull origin main
git checkout -b feature/your-feature-name
```

2. Make your changes following the [Coding Standards](#coding-standards).

3. Write or update tests as needed.

4. Update documentation if applicable.

5. Run the full test suite to ensure everything works.

## Submitting Changes

### Pull Request Process

1. **Ensure all tests pass**:

```bash
pytest tests/ -v
```

2. **Run linting**:

```bash
ruff check dbwarden/
```

3. **Update documentation** for any new features or changes.

4. **Sign your commits with a GPG key**:

   All commits must be signed with a GPG key. Configure Git to sign commits by default:

   ```bash
   git config --global user.signingkey YOUR_GPG_KEY_ID
   git config --global commit.gpgsign true
   ```

   Unsigned commits will be rejected in CI. See [GitHub's GPG signing guide](https://docs.github.com/en/authentication/managing-commit-signature-verification/about-commit-signature-verification) for setup instructions.

5. **Commit your changes** with a clear commit message:

```
feat: Add migration dependency resolution

- Add parse_migration_header() to extract dependencies
- Implement resolve_migration_order() for execution order
- Update migrate command to use resolved order
```

6. **Push to your fork**:

```bash
git push origin feature/your-feature-name
```

7. **Create a Pull Request** against the `main` branch.

8. **Address review feedback** if requested by reviewers.

### Pull Request Requirements

- All tests must pass (100% coverage required)
- Documentation must be updated
- At least one reviewer must approve
- No unresolved conversations
- Commit history should be clean and focused
- All commits must be signed with a GPG Key

### Commit Message Format

Use conventional commit messages:

```
type: short description

- detailed change 1
- detailed change 2
```

Types (both `feat:` and `feature:` are accepted):
- `feat` / `feature`: New functionality
- `fix`: Bug fix
- `refactor`: Code restructuring
- `docs`: Documentation changes
- `test`: Test additions or fixes
- `chore`: Maintenance tasks

Examples:
```
feat: Add colored SQL output in verbose mode

fix: Resolve UNIQUE constraint error on migration retry

docs: Update CLI reference with new --baseline flag
```

## Coding Standards

### Style Guide

- Follow PEP 8 guidelines
- Use type hints for all function signatures
- Keep functions focused and small (max ~50 lines where possible)

### Linting

We use `ruff` for linting:

```bash
# Check for issues
ruff check dbwarden/

# Auto-fix issues
ruff check dbwarden/ --fix
```

### Type Checking

Ensure type hints are correct:

```bash
# Run mypy if configured
mypy dbwarden/
```

### Code Organization

```
dbwarden/
├── cli/           # Command-line interface
├── commands/      # Command implementations
├── database/      # Database operations
├── engine/        # Core engine logic
├── exceptions/    # Custom exceptions
├── logging/       # Logging utilities
└── repositories/  # Data access layer
```

## Testing Requirements

### Test Coverage

- **100% test coverage is required** for all new code
- Use descriptive test names
- Test both success and failure paths

### Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=dbwarden --cov-report=term-missing

# Run specific test file
pytest tests/test_model_discovery.py -v
```

### Writing Tests

- Place tests in the `tests/` directory
- Test file naming: `test_*.py`
- Use pytest framework
- Fixtures should be in `conftest.py`

Example:

```python
def test_migration_with_dependencies():
    """Test that migrations run in correct dependency order."""
    migrations = [
        ("0001", [], ["0002"]),  # 0001 depends on 0002
        ("0002", [], []),
    ]
    resolved = resolve_migration_order(migrations)
    assert resolved[0][0] == "0002"
    assert resolved[1][0] == "0001"
```

## Documentation

### When to Update Docs

- New commands or options
- Changed behavior
- New configuration options
- Bug fixes that affect user workflow

### Documentation Standards

- Use clear, concise language
- Include code examples where helpful
- Update both README and relevant docs in `docs/`
- Run spell check on changes

### Building Documentation

```bash
mkdocs build
mkdocs serve  # For local preview
```

## Code of Conduct

### Our Pledge

In the interest of fostering an open and welcoming environment, we as contributors and maintainers pledge to make participation in our project and our community a harassment-free experience for everyone.

### Our Standards

Examples of behavior that contributes to a positive environment:

- Using welcoming and inclusive language
- Being respectful of differing viewpoints and experiences
- Gracefully accepting constructive criticism
- Focusing on what is best for the community
- Showing empathy towards other community members

Examples of unacceptable behavior:

- The use of sexualized language or imagery
- Trolling, insulting/derogatory comments, and personal or political attacks
- Public or private harassment
- Publishing others' private information without explicit permission

### Enforcement

Instances of abusive, harassing, or otherwise unacceptable behavior may be reported by contacting the project team. All complaints will be reviewed and investigated and will result in a response that is deemed necessary and appropriate.

## License

By contributing to dbwarden, you agree that your contributions will be licensed under the project's MIT License.

---

## Questions?

If you have questions, feel free to open an issue for discussion.
