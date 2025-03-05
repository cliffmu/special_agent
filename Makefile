.PHONY: test lint format clean

test:
	pytest tests/

test-verbose:
	pytest -v tests/

test-coverage:
	pytest --cov=special_agent tests/

lint:
	pylint special_agent/ tests/

format:
	autopep8 --in-place --aggressive --aggressive special_agent/ tests/

clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info
	find . -name "*.pyc" -delete
	find . -name "__pycache__" -delete
	find . -name ".pytest_cache" -delete