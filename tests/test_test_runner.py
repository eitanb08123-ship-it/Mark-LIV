from core.self_improvement import test_runner


def test_passing_test_reports_passed(tmp_path):
    (tmp_path / "test_ok.py").write_text("def test_ok():\n    assert 1 + 1 == 2\n", encoding="utf-8")

    result = test_runner.run_tests(tmp_path)

    assert result.ran is True
    assert result.passed is True
    assert result.exit_code == 0


def test_failing_test_reports_not_passed(tmp_path):
    (tmp_path / "test_bad.py").write_text("def test_bad():\n    assert 1 + 1 == 3\n", encoding="utf-8")

    result = test_runner.run_tests(tmp_path)

    assert result.ran is True
    assert result.passed is False
    assert result.exit_code != 0


def test_target_narrows_to_one_file(tmp_path):
    (tmp_path / "test_pass.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    (tmp_path / "test_fail.py").write_text("def test_fail():\n    assert False\n", encoding="utf-8")

    result = test_runner.run_tests(tmp_path, target="test_pass.py")

    assert result.passed is True  # the failing file was never run


def test_no_tests_collected_is_not_treated_as_passed(tmp_path):
    (tmp_path / "not_a_test.py").write_text("VALUE = 1\n", encoding="utf-8")

    result = test_runner.run_tests(tmp_path)

    assert result.ran is True
    assert result.passed is False
    assert "collected zero tests" in result.stderr
