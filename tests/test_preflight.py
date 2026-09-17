from kermp import preflight


def test_preflight_runs_and_reports():
    lines = preflight.run_preflight()
    assert any("KerMP multiplayer preflight" in line for line in lines)
    assert any("Python environment" in line for line in lines)
    assert any("Sims user folder" in line for line in lines)
    assert any("LAN address:" in line for line in lines)
    assert any("Save path:" in line for line in lines)


def test_preflight_detects_packaged_or_dev():
    lines = preflight.run_preflight()
    assert any("KerMP packaged mod" in line for line in lines)
    assert any("KerMP dev source tree" in line for line in lines)
