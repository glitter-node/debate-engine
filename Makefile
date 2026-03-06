ci:
	./scripts/ci.sh

test:
	APP_ENV=test ./run_manage.sh test

lint:
	@echo "CMD: venv/bin/black --check app/DjangoProto8"
	@ec=0; for f in $$(rg --files app/DjangoProto8 -g '*.py'); do venv/bin/black --check $$f >/dev/null 2>&1 || ec=$$?; done; echo "EXIT:$$ec"; true
	@echo "CMD: venv/bin/black --check app/api"
	@ec=0; for f in $$(rg --files app/api -g '*.py'); do venv/bin/black --check $$f >/dev/null 2>&1 || ec=$$?; done; echo "EXIT:$$ec"; true
	@echo "CMD: venv/bin/black --check app/authflow"
	@ec=0; for f in $$(rg --files app/authflow -g '*.py'); do venv/bin/black --check $$f >/dev/null 2>&1 || ec=$$?; done; echo "EXIT:$$ec"; true
	@echo "CMD: venv/bin/black --check app/thinking"
	@ec=0; for f in $$(rg --files app/thinking -g '*.py'); do venv/bin/black --check $$f >/dev/null 2>&1 || ec=$$?; done; echo "EXIT:$$ec"; true
	@echo "CMD: venv/bin/pylint app/DjangoProto8 app/api app/authflow app/thinking"
	@venv/bin/pylint app/DjangoProto8 app/api app/authflow app/thinking; ec=$$?; echo "EXIT:$$ec"; true

format:
	venv/bin/black app
